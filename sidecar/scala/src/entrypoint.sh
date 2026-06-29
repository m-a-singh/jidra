#!/bin/bash
set -e

REPO=$1
OUTPUT=$2

if [ -z "$REPO" ] || [ -z "$OUTPUT" ]; then
  echo "Usage: entrypoint.sh <repo-path> <output-path>" >&2
  exit 1
fi

# Sync source files into /workspace, preserving target/ for Zinc incremental cache.
# /workspace is a named Docker volume mounted by the caller — it survives across runs.
# rsync copies only changed source files; target/ is untouched so Zinc only recompiles deltas.
WORKDIR=/workspace
mkdir -p "$WORKDIR"
rsync -a --delete \
  --exclude="/target/" \
  --exclude="/.bloop/" \
  --exclude="/.metals/" \
  "$REPO"/ "$WORKDIR"/
cd "$WORKDIR"

# ── Artifactory configuration (mirrors [REDACTED] behaviour) ────
#
# ARTIFACTORY_URL, ARTIFACTORY_USER, ARTIFACTORY_TOKEN are injected by the
# jidra scala extractor via `docker run -e`.  When present, sbt resolves
# everything (boot, plugins, dependencies) through Artifactory instead of
# Maven Central.

if [ -n "$ARTIFACTORY_URL" ]; then
  REPOS_FILE=$(mktemp /tmp/sbt-repositories-XXXXXX)
  cat > "$REPOS_FILE" <<EOF
[repositories]
  local
  artifactory-maven: $ARTIFACTORY_URL/maven
  artifactory-snapshots: $ARTIFACTORY_URL/maven-snapshots
  artifactory-sbt-plugins: $ARTIFACTORY_URL/sbt-plugin-releases-remote, [organization]/[module]/(scala_[scalaVersion]/)(sbt_[sbtVersion]/)[revision]/[type]s/[artifact](-[classifier]).[ext]
EOF

  SBT_OPTS="-Dsbt.override.build.repos=true -Dsbt.repository.config=$REPOS_FILE"

  if [ -n "$ARTIFACTORY_USER" ] && [ -n "$ARTIFACTORY_TOKEN" ]; then
    CREDS_FILE=$(mktemp /tmp/sbt-credentials-XXXXXX)
    ARTIFACTORY_HOST=$(echo "$ARTIFACTORY_URL" | sed 's|https\?://||' | cut -d/ -f1)
    cat > "$CREDS_FILE" <<EOF
realm=Artifactory Realm
host=$ARTIFACTORY_HOST
user=$ARTIFACTORY_USER
password=$ARTIFACTORY_TOKEN
EOF
    SBT_OPTS="$SBT_OPTS -Dsbt.boot.credentials=$CREDS_FILE"
    export COURSIER_CREDENTIALS="$ARTIFACTORY_HOST($ARTIFACTORY_USER:$ARTIFACTORY_TOKEN)"
  fi

  export SBT_OPTS
  echo "[jidra] sbt configured to resolve via $ARTIFACTORY_URL" >&2
else
  echo "[jidra] ARTIFACTORY_URL not set — sbt will use default resolvers" >&2
fi

# ── Scala version detection ──────────────────────────────────────────────────
SCALA3=false
if grep -q 'scalaVersion\s*:=\s*"3\.' build.sbt 2>/dev/null; then
  SCALA3=true
fi

# ── SemanticDB injection ─────────────────────────────────────────────────────
PLUGINS_FILE="project/plugins.sbt"
mkdir -p project

if [ "$SCALA3" = "true" ]; then
  if ! grep -q "semanticdb" build.sbt 2>/dev/null; then
    cat >> build.sbt <<'SCALA3_SETTINGS'

// injected by jidra
ThisBuild / semanticdbEnabled := true
ThisBuild / semanticdbVersion := scalafixSemanticdb.revision
SCALA3_SETTINGS
  fi
else
  if ! grep -q "semanticdb" "$PLUGINS_FILE" 2>/dev/null && ! grep -q "semanticdb" build.sbt 2>/dev/null; then
    cat >> build.sbt <<'SCALA2_SETTINGS'

// injected by jidra
addCompilerPlugin("org.scalameta" % "semanticdb-scalac" % "4.9.9" cross CrossVersion.full)
ThisBuild / scalacOptions ++= Seq("-Yrangepos", s"-P:semanticdb:sourceroot:${(ThisBuild / baseDirectory).value.getAbsolutePath}")
SCALA2_SETTINGS
  fi
fi

echo "[jidra] Running sbt compile to generate SemanticDB files..." >&2
sbt compile 1>&2

echo "[jidra] Copying .semanticdb files to output volume..." >&2
find . -name "*.semanticdb" -type f | while IFS= read -r f; do
  dest="$OUTPUT/$f"
  mkdir -p "$(dirname "$dest")"
  cp "$f" "$dest"
done

COUNT=$(find . -name "*.semanticdb" -type f | wc -l | tr -d ' ')
echo "[jidra] Exported $COUNT .semanticdb files" >&2
