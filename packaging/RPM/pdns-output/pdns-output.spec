Name:       pdns-output
Version:    4.0.0
Release:    3
Summary:    DIM PDNS Output Service
License:    MIT
URL:        https://github.com/ionos-core/dim
Requires:   java-1.8.0-openjdk-headless
BuildRequires: java-1.8.0-openjdk-devel
BuildRequires: systemd-units


Source0: dim-%{version}.tar.gz
Source1: pdns-output.properties
Source2: pdns-output.service

%define debug_package %{nil}

%description
Connects to DIM Database and reads configuration of outputs and events for PowerDNS Databases.

%prep
%setup -q -n dim-%{version}

%build
pushd pdns-output
../gradlew shadowJar
popd

%install
install -Dm 644 pdns-output/pdns-output/build/libs/pdns-output-4.0.0-all.jar %{buildroot}%{_libexecdir}/pdns-output.jar
install -Dm 644 %{SOURCE1} %{buildroot}%{_sysconfdir}/dim/pdns-output.properties
install -Dm 644 %{SOURCE2} %{buildroot}%{_unitdir}/pdns-output.service

%files
%{_libexecdir}/pdns-output.jar
%{_sysconfdir}/dim/pdns-output.properties
%{_unitdir}/pdns-output.service

%changelog
* Thu Oct 14 2021 Christoph Maser <christoph.maser@gmail.com> - 4.0.0-3
- Fix for %{_unitdir}

* Thu Oct 07 2021 Christoph Maser <christoph.maser@gmail.com> - 4.0.0-2
- Update build

* Thu Jul 15 2021 Christoph Maser <christoph.maser@gmail.com> - 4.0.0-1
- Initial package
