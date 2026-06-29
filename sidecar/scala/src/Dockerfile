ARG artifactory_host=repo1.maven.org
FROM docker.$artifactory_host/[REDACTED]:latest
ARG artifactory_host

RUN yum install -y rsync && yum clean all

# Download the sbt launcher jar from Artifactory Maven proxy.
# The launcher is small (~1MB) and version-agnostic — it reads project/build.properties
# at runtime and downloads the declared sbt version from Artifactory.
ARG ARTIFACTORY_URL=https://repo1.maven.org/maven2
ARG SBT_VERSION=1.10.11
RUN curl -fL \
      "$ARTIFACTORY_URL/maven/org/scala-sbt/sbt-launch/$SBT_VERSION/sbt-launch-$SBT_VERSION.jar" \
      -o /usr/local/lib/sbt-launch.jar \
    && printf '#!/bin/bash\nexec java $JAVA_OPTS $SBT_OPTS -jar /usr/local/lib/sbt-launch.jar "$@"\n' \
      > /usr/local/bin/sbt \
    && chmod +x /usr/local/bin/sbt

WORKDIR /app
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh
ENTRYPOINT ["/app/entrypoint.sh"]
