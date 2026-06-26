ARG SBT_VERSION=1.10.11
FROM eclipse-temurin:21-jdk-jammy

# rsync is required by entrypoint.sh to sync sources into the persistent
# /workspace volume while preserving the Zinc incremental-compile cache.
RUN apt-get update && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/*

# Download the sbt launcher jar from Maven Central.
ARG SBT_VERSION
RUN curl -fL \
      "https://repo1.maven.org/maven2/org/scala-sbt/sbt-launch/$SBT_VERSION/sbt-launch-$SBT_VERSION.jar" \
      -o /usr/local/lib/sbt-launch.jar \
    && printf '#!/bin/bash\nexec java $JAVA_OPTS $SBT_OPTS -jar /usr/local/lib/sbt-launch.jar "$@"\n' \
      > /usr/local/bin/sbt \
    && chmod +x /usr/local/bin/sbt

WORKDIR /app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
