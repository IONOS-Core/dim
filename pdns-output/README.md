## DIM PDNS Output Service

Connects to DIM Database and reads configuration of outputs and events for PowerDNS Databases.

Can apply DNSSec Zone signing.

Requires Java 21.

# Build

```bash
cd <dim-checkout>/pdns-output
./build.sh
```

`build.sh` builds and publishes the `jdnssec-dnsjava`, `jdnssec-tools` and
`gmp-rsa` modules to the local maven repository, then builds the fat jar.
`pdns-output-<version>-all.jar` is now in `<dim-checkout>/pdns-output/pdns-output/build/libs`.

# Test

**Never point any of the tests at a production database.** Everything below
runs in throwaway containers.

## Unit tests

`jdnssec-dnsjava` and `gmp-rsa` need nothing. The `pdns-output` tests write to
a `pdns1` database on `127.0.0.1:3307` (`src/test/resources/hikari.properties`)
and axfr the signed zones from a PowerDNS on `127.0.0.1:5153`. The mariadb of
the dim-testsuite provides the database, a second PowerDNS serves it on that
port. The tests run inside mariadb's network namespace, which may have no
internet access, so the Gradle distribution and dependencies are fetched first
into a volume:

```bash
cd <dim-checkout>/dim-testsuite
docker compose up -d --wait mariadb
sed -e 's/^local-address=.*/local-address=127.0.0.1/' -e 's/^local-port=.*/local-port=5153/' \
    docker/pdns1.conf > /tmp/pdns-unit.conf
docker run -d --rm --name pdns-unit --network container:dim-testsuite-mariadb-1 \
    -v /tmp/pdns-unit.conf:/etc/powerdns/pdns.conf:ro powerdns/pdns-auth-50:5.0.3

cd ../pdns-output
# 1. fetch Gradle and the dependencies (needs internet)
docker run --rm -v "$PWD":/src:ro -v pdns-output-gradle:/home/gradle/.gradle gradle:jdk21-jammy \
    bash -c 'cp -r /src /build && cd /build && ./gradlew -q --no-daemon testClasses'
# 2. run the tests of all modules next to mariadb and PowerDNS
docker run --rm --network container:dim-testsuite-mariadb-1 \
    -v "$PWD":/src:ro -v pdns-output-gradle:/home/gradle/.gradle gradle:jdk21-jammy \
    bash -c 'cp -r /src /build && cd /build && ./gradlew --no-daemon --offline test --continue'

# clean up
docker rm -f pdns-unit
cd ../dim-testsuite && docker compose down -v
```

The source is copied into the container, so the checkout is left untouched.
Step 1 is only needed again after a dependency changed.

## dim-testsuite

The test suite in `<dim-checkout>/dim-testsuite` runs dim, ndcli and a
`pdns-output` jar built from the checkout against mariadb and two PowerDNS
instances (the versions used in production). See
[dim-testsuite/README.md](../dim-testsuite/README.md) for details. From
`<dim-checkout>/dim-testsuite`:

```bash
# everything: pytest suite, then the ndcli tests in t/
docker compose run --rm --build testsuite all

# every dnssec related test, then zones signed by pdns-output are validated
# with drill and jdnssec-verifyzone
docker compose run --rm --build testsuite dnssec

# a single pytest file or test; the mount picks up local changes to tests/
# without rebuilding
docker compose run --rm -v ./tests:/src/dim-testsuite/tests testsuite \
    bash -c 'make db-clear && virtual_env/bin/pytest -v tests/dnssec_test.py'

# a few ndcli tests
docker compose run --rm -e TESTS="zone-dnssec-attrs.t rr-create-ds-1.t" testsuite ndcli

docker compose down -v
```

`--build` is needed after changing `dim/`, `ndcli/`, `dimclient/`,
`dim-testsuite/` or `pdns-output/`, the image contains a copy of the checkout.

### comparing against a previous build

`dnssec-diff` runs a previous `pdns-output` jar (on java 8) and the current one
(on java 21) over the same dim database and diffs what both write to PowerDNS.
With identical behaviour the records, RRSIGs included, are byte for byte the
same. Use it before releasing changes to the signing code or its dependencies.
The jar has to be built from the previous version, e.g. from the last release
tag with `gradle:jdk8-jammy` and that version's `Dockerfile`:

```bash
cd <dim-checkout>/dim-testsuite
mkdir -p baseline && cp <old>/pdns-output-4.0.0-all.jar baseline/pdns-output-old.jar
docker compose run --rm --build -v ./baseline:/opt/baseline:ro -v ./out:/out testsuite dnssec-diff
```

Results, dumps and diffs are in `out/dnssec-diff/`. The JVM timezone matters:
the 4.0.0 jar read the signature validity window from dim in the JVM's local
timezone, so on a non-UTC JVM its RRSIG inception/expiration differ from the
current jar's.

# Docker build
Build your docker image by running:

```bash
docker build . -t pdns-output:latest
```

# Docker Run
Config files are located in [log4j2.properties](./log4j2.properties) and [pdns-output.properties](./pdns-output.properties)
Update these files and mount them to  ```/etc/dim/```

```bash
docker run \
  -v ./pdns-output.properties:/etc/dim/pdns-output.properties:ro \
  -v ./log4j2.properties:/etc/dim/log4j2.properties:ro \
  pdns-output:latest
```

# RPM package (OBS)

The spec, systemd unit and sysconfig file are in [obs/](./obs). OBS builds
without network access, so besides the source tarball the package carries a
tarball with the Gradle distribution and every dependency.

## 1. Prepare the sources

`Version` in `obs/pdns-output.spec` has to match `version` in
`pdns-output/build.gradle`. Commit your changes first, the source tarball is
made from git.

```bash
VERSION=5.0.3
OUT=/tmp/obs-pdns-output
mkdir -p $OUT
cd <dim-checkout>

# Source0: the pdns-output directory of the committed branch
git archive --prefix=pdns-output-$VERSION/ HEAD:pdns-output | gzip > $OUT/pdns-output-$VERSION.tar.gz

# Source1: Gradle distribution and dependency cache, needs internet access.
# Regenerate it whenever build.gradle, a dependency or the Gradle wrapper changes.
docker run --rm -v $OUT:/out -e VERSION=$VERSION -e OWNER=$(id -u):$(id -g) gradle:jdk21-jammy bash -c '
  set -e
  mkdir /build && tar -xzf /out/pdns-output-$VERSION.tar.gz -C /build --strip-components=1 && cd /build
  export HOME=/tmp/h GRADLE_USER_HOME=/tmp/h/.gradle && mkdir -p $HOME
  ./build.sh > /out/offline-cache-build.log 2>&1
  ./gradlew --stop > /dev/null
  rm -rf $GRADLE_USER_HOME/daemon $GRADLE_USER_HOME/.tmp
  tar -czf /out/gradle-offline-$VERSION.tar.gz -C $HOME .gradle
  chown $OWNER /out/*'

cp pdns-output/obs/pdns-output.spec pdns-output/obs/pdns-output.service pdns-output/obs/pdns-output.sysconfig $OUT/
```

The cache tarball is about 300 MB.

## 2. Test build without OBS (optional)

Builds the spec on Oracle Linux 9 with networking disabled, the way OBS builds
it, so a missing dependency in the cache tarball shows up here:

```bash
docker build -t pdns-output-rpmbuild - <<'EOF'
FROM oraclelinux:9
RUN dnf -y install rpm-build java-21-openjdk-devel unzip systemd-rpm-macros && dnf clean all
RUN useradd -m builder
USER builder
WORKDIR /home/builder
EOF

mkdir -p /tmp/obs-rpms && chmod 777 /tmp/obs-rpms
docker run --rm --network none -v $OUT:/src:ro -v /tmp/obs-rpms:/out pdns-output-rpmbuild bash -c '
  mkdir -p rpmbuild/SOURCES rpmbuild/SPECS
  cp /src/*.tar.gz /src/pdns-output.service /src/pdns-output.sysconfig rpmbuild/SOURCES/
  cp /src/pdns-output.spec rpmbuild/SPECS/
  rpmbuild -bb rpmbuild/SPECS/pdns-output.spec && cp rpmbuild/RPMS/noarch/*.rpm /out/'

# install it on a clean system and run it as the service user
docker run --rm -v /tmp/obs-rpms:/rpms:ro oraclelinux:9 bash -c '
  dnf -y -q install /rpms/pdns-output-*.noarch.rpm &&
  su -s /bin/bash pdns-output -c "/usr/sbin/pdns-output --help"'
```

## 3. Build and push with osc

`osc` only works inside a package checkout of the OBS project (a directory with
`.osc/`), not in `obs/` of this repository:

```bash
cd ~/ionos/ITODNS:EPEL:Staging:A      # osc checkout of the project
osc up
osc mkpac pdns-output                 # first time only, if the package does not exist yet
cd pdns-output

cp $OUT/pdns-output.spec $OUT/pdns-output.service $OUT/pdns-output.sysconfig .
cp $OUT/pdns-output-$VERSION.tar.gz $OUT/gradle-offline-$VERSION.tar.gz .
rm -f pdns-output-<old-version>.tar.gz gradle-offline-<old-version>.tar.gz   # on version bumps

# local build in the OBS build environment
osc build --clean --no-verify OracleLinux_9 x86_64

# push: register added/removed files and commit, OBS then builds the package
osc addremove
osc status
osc commit -m "pdns-output $VERSION"
```

`osc results` shows the build state on the server, `osc buildlog OracleLinux_9 x86_64`
the log. Promote from `ITODNS:EPEL:Staging:A` to `ITODNS:EPEL` only after the
package was tested on a staging host.

## Upgrading from the 4.x RPM

The 5.x package is laid out differently from the old `packaging/RPM` one:

| | 4.x | 5.x |
|---|---|---|
| Java | 1.8 | 21 (`java-21-openjdk-headless`) |
| jar | `/usr/libexec/pdns-output.jar` | `/usr/share/java/pdns-output/pdns-output.jar` |
| started by | `/bin/java -jar ...` as root | `/usr/sbin/pdns-output` as user `pdns-output` |
| JVM options | in the unit file | `JAVA_OPTS` in `/etc/sysconfig/pdns-output` |
| `useNativeCrypto` default | `true` | `false` |

Before upgrading a host, back up `/etc/dim/pdns-output.properties`. Afterwards
check for a `.rpmnew` / `.rpmsave` next to it, make sure the file still holds
the right database credentials and is readable by the `pdns-output` group, and
set `useNativeCrypto=true` again if the host should keep using the gmp based
signing (both produce identical signatures, gmp is faster). If the JVM's
timezone is not UTC, signatures created after the upgrade use the validity
window dim intended instead of one shifted by the UTC offset; existing ones are
replaced at the next re-sign of the zone.
