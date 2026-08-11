# Setup DB, DIM and PDNS Server and Shell environment

The following steps assume that you have a minimal EL8 installed.

## Disable SELINUX

```
# setenforce 0
# echo -e "SELINUX=disabled\nSELINUXTYPE=targeted" >/etc/sysconfig/selinux
# systemctl stop firewalld
# systemctl disable firewalld
```

This is not a tutorial on Linux Security Technologies, so just disable it.

## Networking

Setup additional IPs:

```
# dnf install network-scripts
# systemctl enable network.service

# cat <<EOF >/etc/sysconfig/network-scripts/ifcfg-lo-pdns-int
DEVICE=lo
IPADDR=127.1.0.1
NETMASK=255.0.0.0
NETWORK=127.0.0.0
BROADCAST=127.255.255.255
ONBOOT=yes
NAME=loopback1
NM_CONTROLLED=no
EOF

# cat <<EOF >/etc/sysconfig/network-scripts/ifcfg-lo-pdns-pub
DEVICE=lo
IPADDR=127.2.0.1
NETMASK=255.0.0.0
NETWORK=127.0.0.0
BROADCAST=127.255.255.255
ONBOOT=yes
NAME=loopback2
NM_CONTROLLED=no
EOF

# cat <<EOF >/etc/sysconfig/network-scripts/ifcfg-lo-pdns-rec-int
DEVICE=lo
IPADDR=127.3.0.1
NETMASK=255.0.0.0
NETWORK=127.0.0.0
BROADCAST=127.255.255.255
ONBOOT=yes
NAME=loopback3
NM_CONTROLLED=no
EOF

# cat <<EOF >/etc/sysconfig/network-scripts/ifcfg-lo-bind-int
DEVICE=lo
IPADDR=127.4.0.1
NETMASK=255.0.0.0
NETWORK=127.0.0.0
BROADCAST=127.255.255.255
ONBOOT=yes
NAME=loopback4
NM_CONTROLLED=no
EOF
```

## Install EPEL

`# dnf install epel-release`

Install necessary tools

`# dnf install wget bind-utils`

## MariaDB

### Setup repository

```
# cat <<EOF >/etc/yum.repos.d/mariadb.repo
[mariadb]
name = MariaDB
baseurl = http://yum.mariadb.org/10.5/rhel8-amd64
gpgkey=https://yum.mariadb.org/RPM-GPG-KEY-MariaDB
gpgcheck=1
module_hotfixes=1
EOF
```

### install Software

```
# dnf install MariaDB-server

# systemctl enable mariadb
# systemctl start mariadb

# mysql -u root
create database dim;
create database pdns_int;
create database pdns_pub;
-- create users
grant all on dim.* to dim_user@localhost identified by 'dim_pass';
grant insert,update,delete,select on pdns_int.* to dim_pdns_int_user@localhost identified by 'SuperSecret1';
grant insert,update,delete,select on pdns_pub.* to dim_pdns_pub_user@localhost identified by 'SuperSecret2';
grant select on pdns_pub.* to pdns_pub_user@localhost identified by 'SuperSecret3';
grant select on pdns_int.* to pdns_int_user@localhost identified by 'SuperSecret4';
```

### create tables for pdns

```
# wget -O - https://raw.githubusercontent.com/ionos-core/dim/master/dim/pdns.sql | mysql -u root pdns_int
# wget -O - https://raw.githubusercontent.com/ionos-core/dim/master/dim/pdns.sql | mysql -u root pdns_pub
```

## PowerDNS

Install repo file and install software

```
# curl -o /etc/yum.repos.d/powerdns-auth-44.repo https://repo.powerdns.com/repo-files/el-auth-44.repo
# dnf install pdns-tools pdns-backend-mysql
```

Fix config

```
# rm -f /etc/pdns/pdns.conf
```

### Setup two PowerDNS instances

internal pdns

```
# mkdir -p /etc/pdns/{int,pub}

# cat <<EOF >/etc/pdns/pdns-int.conf
setgid=pdns
setuid=pdns
version-string=powerdns
local-port=53
guardian=no
daemon=no
slave=no
master=no
expand-alias=yes
resolver=127.3.0.1
8bit-dns=yes
max-tcp-connections=300
local-address=127.1.0.1
disable-axfr=no
allow-axfr-ips=127.0.0.0/8,::1
cache-ttl=10
query-cache-ttl=20
negquery-cache-ttl=60
queue-limit=200
max-cache-entries=2000
max-queue-length=1000
udp-truncation-threshold=1220
webserver=no
tcp-control-port=
log-timestamp=no
logging-facility=6
loglevel=3
query-logging=no
log-dns-details=no

# backend
launch=gmysql
gmysql-dnssec
gmysql-socket=/var/lib/mysql/mysql.sock
gmysql-dbname=pdns_int
gmysql-user=pdns_int_user
gmysql-password=SuperSecret4
EOF
```

public pdns

```
# cat <<EOF >/etc/pdns/pdns-pub.conf
setgid=pdns
setuid=pdns
version-string=powerdns
local-port=53
guardian=no
daemon=no
slave=no
master=no
expand-alias=yes
resolver=127.3.0.1
8bit-dns=yes
max-tcp-connections=300
disable-axfr=no
allow-axfr-ips=127.0.0.0/8,::1
cache-ttl=10
query-cache-ttl=20
negquery-cache-ttl=60
queue-limit=200
max-cache-entries=2000
max-queue-length=1000
udp-truncation-threshold=1220
webserver=no
tcp-control-port=
log-timestamp=no
logging-facility=6
loglevel=3
query-logging=no
log-dns-details=no
local-address=127.2.0.1
launch=gmysql
gmysql-dnssec
gmysql-socket=/var/lib/mysql/mysql.sock
gmysql-dbname=pdns_pub
gmysql-user=pdns_pub_user
gmysql-password=SuperSecret3
EOF
```

Now start both pdns instances::

```
# systemctl start pdns@int
# systemctl start pdns@pub
```

use systemctl status to verify that startup worked.

```
# systemctl enable pdns@int
# systemctl enable pdns@pub
```

## PowerDNS Recursor

Install pdns-recursor software

```
# curl -o /etc/yum.repos.d/powerdns-rec-45.repo https://repo.powerdns.com/repo-files/el-rec-45.repo

# dnf install pdns-recursor
```

remove default config

```
# rm -f /etc/pdns-recursor/recursor.conf
```

Setup instance

```
# mkdir -p /etc/pdns-recursor/int

$ cat <<EOF  >/etc/pdns-recursor/recursor-int.conf
allow-from=0.0.0.0/0, ::/0
any-to-tcp=yes
client-tcp-timeout=5
disable-packetcache=no
dnssec=process
dont-query=127.0.0.0/8,100.64.0.0/10,169.254.0.0/16,192.0.0.0/24,192.0.2.0/24,198.51.100.0/24,203.0.113.0/24,240.0.0.0/4,::1/128,::ffff:0:0/96,100::/64,2001:db8::/32
entropy-source=/dev/urandom
export-etc-hosts=no
forward-zones-file=/etc/pdns-recursor/int/forward.zones
latency-statistic-size=10000
local-address=127.3.0.1
local-port=53
logging-facility=6
loglevel=4
lua-config-file=/etc/pdns-recursor/int/nta.lua
log-common-errors=no
max-cache-entries=8000
max-cache-ttl=86400
max-mthreads=2048
max-negative-ttl=600
max-packetcache-entries=8000
max-qperq=50
max-tcp-clients=300
max-tcp-per-client=0
max-total-msec=7000
minimum-ttl-override=0
network-timeout=1970
no-shuffle=off
packetcache-ttl=120
packetcache-servfail-ttl=15
pdns-distributes-queries=no
processes=1
query-local-address=0.0.0.0 ::
quiet=on
root-nx-trust=on
serve-rfc1918=on
server-down-max-fails=64
server-down-throttle-time=60
server-id=dim-rec-int
setgid=pdns-recursor
setuid=pdns-recursor
single-socket=off
spoof-nearmiss-max=20
stack-size=200000
stats-ringbuffer-entries=200000
trace=off
udp-truncation-threshold=1220
edns-outgoing-bufsize=1220
version-string=PowerDNS-Recursor
EOF

# cat <<EOF >/etc/pdns-recursor/int/forward.zones
+example.com=127.1.0.1
+internal.local=127.1.0.1
EOF

# cat <<EOF >/etc/pdns-recursor/int/nta.lua
addNTA('internal.local')
addNTA('example.com')
EOF
```

Enable and start the service, please verify service health using `journalctl`

```
# systemctl enable pdns-recursor@int
# systemctl start pdns-recursor@int
```

## DIM

Install rpms of dim, dimclient, ndcli and jdk::

```
# mkdir -p /etc/dim /srv/http/dim.example.com
# dnf install https://github.com/ionos-core/dim/releases/download/dim-4.0.9/dim-4.0.9-1.el8.x86_64.rpm
# dnf install https://github.com/ionos-core/dim/releases/download/dimclient-0.4.5/python3-dimclient-0.4.5-1.el8.x86_64.rpm
# dnf install https://github.com/ionos-core/dim/releases/download/ndcli-4.0.3/python3-ndcli-4.0.3-1.el8.x86_64.rpm
```

pdns-output needs to be build manually at the moment (any volunteers?)

```
# dnf install git java-1.8.0-openjdk-devel
# git clone https://github.com/ionos-core/dim
# cd dim/pdns-output
# cd jdnssec-dnsjava && ../../gradlew build -x test && ../../gradlew publishToMavenLocal; cd ..
# cd jdnssec-tools && ../../gradlew build -x test && ../../gradlew publishToMavenLocal; cd ..
# cd gmp-rsa && ../../gradlew build -x test && ../../gradlew publishToMavenLocal; cd ..
# cd pdns-output && ../../gradlew shadowJar -x test; cd ..
# cp pdns-output/build/libs/pdns-output-4.0.0-all.jar /opt/dim
# cd ..
# rm -rf dim
```

systemd unit file

```
# cat <<EOF >/etc/systemd/system/pdns-output.service
[Unit]
Description=DIM to PowerDNS DB
After=network.target mysql.target

[Service]
Type=simple
ExecStart=/bin/java -jar /opt/dim/pdns-output.jar
Restart=on-failure
StartLimitInterval=0
PrivateTmp=true
PrivateDevices=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE CAP_SETGID CAP_SETUID CAP_CHOWN CAP_SYS_CHROOT
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LimitNOFILE=40000

[Install]
WantedBy=multi-user.target
EOF
```

configure `dim.cfg`

```
# cat <<EOF >/etc/dim/dim.cfg
DB_USERNAME = 'dim_user'
DB_PASSWORD = 'dim_pass'
DB_HOST     = 'localhost'
SQLALCHEMY_DATABASE_URI = 'mysql://%s:%s@%s/dim' % (DB_USERNAME, DB_PASSWORD, DB_HOST)
DB_LOCK_TIMEOUT = 120
SECRET_KEY = 'SuperSecretTtestkey'

### Authentication
# 'ldap' or None
AUTHENTICATION_METHOD = None

LDAP_SERVER = "ldap://testldap"
LDAP_USER_DN = "uid=%s"
LDAP_SEARCH_BASE = ""

# Used by manage_dim ldap_sync
LDAP_USER_BASE = ""
LDAP_DEPARTMENT_BASE = ""

# Set SECRET_KEY to a random string
# The security of this application is compromised if SECRET_KEY is leaked
SECRET_KEY = 'testkey'

PERMANENT_SESSION_LIFETIME = 5 * 24 * 3600 # 5 days
TEMPORARY_SESSION_LIFETIME = 24 * 3600 # 1 day

### Logging
import logging, logging.handlers, sys

LOGGING_LEVEL = logging.DEBUG
LOGGING_HANDLER = logging.StreamHandler(sys.stderr)

### DNS
DNS_DEFAULT_REFRESH  = 14400  # 4 hours
DNS_DEFAULT_RETRY    = 3600   # 1 hour
DNS_DEFAULT_EXPIRE   = 605000 # 7 days
DNS_DEFAULT_MINIMUM  = 60     # 1 minute
DNS_DEFAULT_ZONE_TTL = 86400  # 1 day (Default. TTL)

# list of ipspaces which are allowed to exist multiple times in dim (layer3domains)
# in general only rfc1918 ip should be allowed
LAYER3DOMAIN_WHITELIST = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10']
EOF
```

Install apache httpd and mod_wsgi

```
# dnf install python3-mod_wsgi mod_ssl
# echo "" > /etc/httpd/conf.d/welcome.conf
```

setup /opt/dim/dim.wsgi

```
# cat <<EOF >/opt/dim/dim.wsgi
from dim import create_app
application = create_app()
EOF
```

setup wsgi.conf

```
# cat <<EOF >/etc/httpd/conf.d/dim.example.com.conf
<VirtualHost *:80>
  ServerName dim.example.com
  ServerAlias localhost
  ServerAdmin dim@example.com
  ErrorLog /var/log/httpd/error_log_dim.example.com
  CustomLog /var/log/httpd/access_log_dim.example.com combined
  LogLevel error
  DocumentRoot /srv/http/dim.example.com
  WSGIDaemonProcess dim python-home=/opt/dim
  WSGIScriptAlias /dim /opt/dim/dim.wsgi
  <Directory /srv/http/dim.example.com>
    RewriteEngine on
    RewriteCond %{REQUEST_FILENAME} -f [OR]
    RewriteCond %{REQUEST_FILENAME} -d
    RewriteRule ^ - [L]
    RewriteRule ^ index.html [L]
    Require all granted
  </Directory>
  <Directory /opt/dim>
    Require all granted
  </Directory>
  Alias /dim/doc  /opt/dim/doc
  Alias /netdot/doc  /opt/dim/doc
  <Location /dim>
    WSGIProcessGroup dim
    Require all granted
  </Location>
</VirtualHost>
EOF
```

Setting up SSL is left as an exercise to the reader.

start apache `systemctl enable httpd; systemctl start httpd`

Run `/opt/dim/bin/manage_db init` to create tables

create a ``.ndclirc`` in your home:

```
cat <<EOF >~/.ndclirc
server=http://localhost/dim
username=user
EOF
```

Give the user `user` uberadmin rights `/opt/dim/bin/manage_dim set_user -u user -t Admin` and force re login `rm -f ~/.ndcli.cookie; ndcli login`.

make sure that ``bash-completion`` is installed (to enable ``ndcli`` completion)

`# dnf install bash-completion`

run ``ndcli show server-info`` to test connection to DIM. Just hit enter if a password is asked. Should output information about python and db used.

## DIM output

config file

```
# cat <<EOF >/etc/dim/pdns-output.properties
# dim database connection parameters
db.serverName=127.0.0.1
db.portNumber=3306
db.databaseName=dim
db.user=dim_user
db.password=dim_pass

# Timeout in seconds for getting the pdns_poller lock which prevents multiple pdns-output instances from running
lockTimeout=120

# Delay in seconds used when polling the dim outputupdate table
pollDelay=1

# Delay in seconds before retrying a failed update
retryInterval=60

# Debug option to print to stdout transaction ids after processing them
printTxn=false

# Max size of a sql query in bytes
# should be less than the configured max_allowed_packet in mysql
maxQuerySize=4000000

useNativeCrypto=true
EOF
```

Enable and start service pdns-output

```
# systemctl enable pdns-output
# systemctl start pdns-output
```

Now the system is setup completely and you can start exploring the usage of DIM/ndcli.
