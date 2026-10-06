# syntax=docker/dockerfile:1
FROM gradle:jdk21-jammy as builder

WORKDIR /build

COPY . .

RUN cd ./jdnssec-dnsjava \
    && ../gradlew build -x test \
    && ../gradlew publishToMavenLocal

RUN cd ./jdnssec-tools \
    && ../gradlew build -x test \
    && ../gradlew publishToMavenLocal

RUN cd ./gmp-rsa \
    && ../gradlew build -x test \
    && ../gradlew publishToMavenLocal

RUN cd ./pdns-output \
    && ../gradlew shadowJar -x test


FROM eclipse-temurin:21-jre AS final

ARG UID=10001
RUN adduser \
    --disabled-password \
    --gecos "" \
    --home "/nonexistent" \
    --shell "/sbin/nologin" \
    --no-create-home \
    --uid "${UID}" \
    pdns-output

RUN mkdir /etc/dim \
    && chown pdns-output:pdns-output /etc/dim
COPY --chown=pdns-output:pdns-output ./pdns-output/src/main/resources/* /etc/dim/

USER pdns-output

ARG VERSION=5.0.3

COPY --from=builder /build/pdns-output/build/libs/pdns-output-${VERSION}-all.jar /opt/pdns-output.jar

ENTRYPOINT [ "java", "-jar", "/opt/pdns-output.jar" ]
