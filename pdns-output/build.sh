#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"

cd jdnssec-dnsjava && ../gradlew build -x test && ../gradlew publishToMavenLocal; cd ..

cd jdnssec-tools && ../gradlew build -x test && ../gradlew publishToMavenLocal; cd ..

cd gmp-rsa && ../gradlew build -x test && ../gradlew publishToMavenLocal; cd ..

cd pdns-output && ../gradlew shadowJar -x test; cd ..
