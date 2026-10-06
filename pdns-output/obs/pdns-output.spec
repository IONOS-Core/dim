#
# spec file for package pdns-output
#
# Copyright (c) 2026 IONOS SE
#

Name:           pdns-output
Version:        5.0.3
Release:        0
Summary:        DIM PDNS Output Service
License:        Proprietary
Group:          System/Daemons
URL:            https://github.com/ionos-cloud/dim

# git archive --prefix=%{name}-%{version}/ HEAD | gzip > %{name}-%{version}.tar.gz
Source0:        %{name}-%{version}.tar.gz

# Offline Gradle wrapper distribution + Maven dependency cache.
# Generate once in a clean environment (requires internet access):
#
#   GRADLE_HOME_TMP=$(mktemp -d)
#   export HOME=${GRADLE_HOME_TMP}
#   export GRADLE_USER_HOME=${GRADLE_HOME_TMP}/.gradle
#   ./build.sh
#   tar -czf gradle-offline-%{version}.tar.gz -C ${GRADLE_HOME_TMP} .gradle .m2
#
# The tarball must contain .gradle/ (wrapper distribution + dependency caches)
# and .m2/ (Maven local repository with external dependencies pre-warmed from
# the publishToMavenLocal steps).
Source1:        gradle-offline-%{version}.tar.gz

Source2:        %{name}.service
Source3:        %{name}.sysconfig

BuildRequires:  java-21-openjdk-devel
BuildRequires:  unzip
BuildRequires:  systemd-rpm-macros
BuildArch:      noarch

Requires:       java-21-openjdk-headless
Requires(pre):  shadow
Requires(post): systemd
Requires(preun): systemd
Requires(postun): systemd

%description
pdns-output connects to the DIM (Domain Information Management) database
and reads the configuration of outputs and events for PowerDNS databases.
It polls the DIM outputupdate table and applies incremental changes to the
configured PowerDNS MySQL backends, with optional DNSSEC zone signing.

Configuration is read from /etc/dim/pdns-output.properties; logging is
controlled by /etc/dim/log4j2.properties.

%prep
%setup -q

# Restore the offline Gradle wrapper distribution and Maven local cache into
# a scratch HOME so the build never touches the internet.
export _gradle_home=%{_builddir}/_gradle_home
mkdir -p ${_gradle_home}
tar -xzf %{SOURCE1} -C ${_gradle_home}

%build
export HOME=%{_builddir}/_gradle_home
export GRADLE_USER_HOME=%{_builddir}/_gradle_home/.gradle

cd jdnssec-dnsjava
../gradlew build            -x test --no-daemon --offline
../gradlew publishToMavenLocal      --no-daemon --offline
cd ..

cd jdnssec-tools
../gradlew build            -x test --no-daemon --offline
../gradlew publishToMavenLocal      --no-daemon --offline
cd ..

cd gmp-rsa
../gradlew build            -x test --no-daemon --offline
../gradlew publishToMavenLocal      --no-daemon --offline
cd ..

cd pdns-output
../gradlew shadowJar        -x test --no-daemon --offline
cd ..

%install
# Fat JAR
install -Dm 0644 pdns-output/build/libs/%{name}-%{version}-all.jar \
    %{buildroot}%{_javadir}/%{name}/%{name}.jar

# Default configuration (marked noreplace so local edits survive upgrades)
install -Dm 0640 pdns-output/src/main/resources/pdns-output.properties \
    %{buildroot}%{_sysconfdir}/dim/pdns-output.properties
install -Dm 0640 pdns-output/src/main/resources/log4j2.properties \
    %{buildroot}%{_sysconfdir}/dim/log4j2.properties

# Systemd unit and sysconfig defaults
install -Dm 0644 %{SOURCE2} %{buildroot}%{_unitdir}/%{name}.service
install -Dm 0640 %{SOURCE3} %{buildroot}%{_sysconfdir}/sysconfig/%{name}

# Launcher wrapper — sources sysconfig so operators can tune JAVA_OPTS etc.
install -dm 0755 %{buildroot}%{_sbindir}
cat > %{buildroot}%{_sbindir}/%{name} << 'WRAPPER'
#!/bin/bash
[ -f /etc/sysconfig/pdns-output ] && . /etc/sysconfig/pdns-output
exec /usr/bin/java \
    ${JAVA_OPTS} \
    -Dlog4j.configurationFile=${LOG4J2_CONFIG:-/etc/dim/log4j2.properties} \
    -jar %{_javadir}/%{name}/%{name}.jar \
    -c ${CONFIG_FILE:-/etc/dim/pdns-output.properties} \
    "$@"
WRAPPER
chmod 0755 %{buildroot}%{_sbindir}/%{name}

# Runtime state directory owned by service user
install -dm 0750 %{buildroot}%{_localstatedir}/lib/%{name}

%pre
getent group  %{name} >/dev/null || groupadd -r %{name}
getent passwd %{name} >/dev/null || \
    useradd -r -g %{name} \
            -d %{_localstatedir}/lib/%{name} \
            -s /sbin/nologin \
            -c "DIM PDNS Output daemon" \
            %{name}
exit 0

%post
%systemd_post %{name}.service

%preun
%systemd_preun %{name}.service

%postun
%systemd_postun_with_restart %{name}.service

%files
%defattr(-,root,root,-)
%dir %{_javadir}/%{name}
%{_javadir}/%{name}/%{name}.jar
%{_sbindir}/%{name}
%{_unitdir}/%{name}.service
%dir %{_sysconfdir}/dim
# the service runs as %{name}, which has to read its configuration
%config(noreplace) %attr(0640,root,%{name}) %{_sysconfdir}/dim/pdns-output.properties
%config(noreplace) %attr(0640,root,%{name}) %{_sysconfdir}/dim/log4j2.properties
%config(noreplace) %attr(0640,root,%{name}) %{_sysconfdir}/sysconfig/%{name}
%attr(0750,%{name},%{name}) %dir %{_localstatedir}/lib/%{name}

%changelog
