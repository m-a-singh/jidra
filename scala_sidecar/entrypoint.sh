#!/bin/bash
set -e

REPO=$1
OUTPUT=$2

if [ -z "$REPO" ] || [ -z "$OUTPUT" ]; then
  echo "Usage: entrypoint.sh <repo-path> <output-path>" >&2
  exit 1
fi

# Sync source files into /workspace, preserving target/ for Zinc incremental cache.
# /workspace is a named Docker volume mounted by the caller — it survives across runs,
# so repeated indexing of the same codebase only recompiles what changed instead of
# rebuilding from scratch every time. rsync copies only changed source files; target/
# is excluded so Zinc's own cache there is untouched.
WORKDIR=/workspace
mkdir -p "$WORKDIR"
rsync -a --delete \
  --exclude="/target/" \
  --exclude="/.bloop/" \
  --exclude="/.metals/" \
  "$REPO"/ "$WORKDIR"/
cd "$WORKDIR"


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
