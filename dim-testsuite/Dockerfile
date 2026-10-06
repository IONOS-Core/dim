# syntax=docker/dockerfile:1
#
# Image running the dim test suites. The build context is the repository root:
#
#   docker build -f dim-testsuite/Dockerfile ..
#
# See dim-testsuite/compose.yaml for the whole environment.

# the same build as pdns-output/Dockerfile, so the suite tests the jar built
# from the current checkout
FROM gradle:jdk21-jammy AS jar-builder

WORKDIR /build
COPY pdns-output/ ./

RUN cd ./jdnssec-dnsjava \
    && ../gradlew --no-daemon build -x test \
    && ../gradlew --no-daemon publishToMavenLocal

RUN cd ./jdnssec-tools \
    && ../gradlew --no-daemon build -x test \
    && ../gradlew --no-daemon publishToMavenLocal

RUN cd ./gmp-rsa \
    && ../gradlew --no-daemon build -x test \
    && ../gradlew --no-daemon publishToMavenLocal

RUN cd ./pdns-output \
    && ../gradlew --no-daemon shadowJar -x test


FROM python:3.11-slim-bookworm

# pdns-output and the dnssec verification in tests/pdns_util.py need a jre
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH=$JAVA_HOME/bin:$PATH
COPY --from=eclipse-temurin:21-jre $JAVA_HOME $JAVA_HOME
# the jre the pre-java-21 pdns-output ran on, used by docker/dnssec-diff.sh to
# run the baseline jar
COPY --from=eclipse-temurin:8-jre /opt/java/openjdk /opt/java8

# dnsutils: dig, ldnsutils: drill (both used by tests in t/)
# mariadb-client: mysql and mysqldump, used by runtest.py
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        gcc \
        pkg-config \
        default-libmysqlclient-dev \
        mariadb-client \
        dnsutils \
        ldnsutils \
        colordiff \
        make \
        procps \
        libgmp10 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1

WORKDIR /src

# dependencies first, so that source changes don't invalidate them
COPY dim/requirements.txt dim/requirements.txt
COPY ndcli/requirements.txt ndcli/requirements.txt
RUN python -m venv /src/dim-testsuite/virtual_env \
    && /src/dim-testsuite/virtual_env/bin/pip install --no-cache-dir \
        -r dim/requirements.txt \
        -r ndcli/requirements.txt \
        'pytest~=7.1' 'pytest-cov~=3.0'

COPY dim/ dim/
COPY dimclient/ dimclient/
COPY ndcli/ ndcli/
RUN /src/dim-testsuite/virtual_env/bin/pip install --no-cache-dir --no-deps \
        ./dim ./dimclient ./ndcli

COPY dim-testsuite/ dim-testsuite/

RUN mkdir -p /opt/dim /etc/dim
# tests/pdns_util.py falls back to /opt/dim/pdns-output.jar
COPY --from=jar-builder /build/pdns-output/build/libs/ /opt/dim/libs/
RUN set -eu \
    && mv "$(ls /opt/dim/libs/pdns-output-*-all.jar | tail -1)" /opt/dim/pdns-output.jar \
    && rm -rf /opt/dim/libs

COPY dim-testsuite/docker/dim.cfg /etc/dim/dim.cfg

WORKDIR /src/dim-testsuite
ENTRYPOINT ["/src/dim-testsuite/docker/entrypoint.sh"]
CMD ["all"]
