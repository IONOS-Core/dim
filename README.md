# DIM - DNS and IP Management (and also DHCP)

DIM can be used as IP Management for a companies whole IP address space (e.g. RFC1918, public IPv4, ULA IPv6, public IPv6 (GUA), Multicast IPs, ...).

DIM can be used to manage all DNS reverse entries for all IP address space.

DIM allows to document subnets with their vlan id and gateway in a way that this information can be reused for automatic IP configuration on devices.

DIM simplifies the steps "mark ip as used, create forward record, create reverse entry, reload changed zones" to a single line in your preferred shell.

DIM provides an API to allow products to consume and return single IPv4 addresses or whole /64 or /56 prefixes for IPv6.

## Quickstart / Tutorial

Download [VM](https://github.com/ionos-core/dim/releases/download/vm-1.0/dim-4-0-9.qcow2) ([Documentation](VM-SETUP.md) how the VM was created). The VM is preconfigured including PowerDNS and PowerDNS recursor so that you
can immediately check whether your commands had effects.

Read [Tutorial](TUTORIAL.md) to see how DIM can be used to document Prefixes and manage DNS Records.

## Docker

Docker files are available for below components, and you need to do docker build on your own (There is no pre build docker version at this moment)

### DIM

Just mount your dim.cfg to `/etc/dim/dim.cfg`

[Docker file](./dim/Dockerfile)

### ndcli

To build ndcli you need to be in project directory; because there is dependecy to dimclient

```sh
docker build -f ndcli/Dockerfile .
```

| ENV            | Default |
|       ---      | ---     |
| NDCLI_USERNAME | -       |
| NDCLI_SERVER   | -       |

[Docker file](./ndcli/Dockerfile)

## PDNS Output

Mount configuratin to `/etc/dim/pdns-output.properties`

[Docker file](./pdns-output/Dockerfile)

## Docker compose (All-in-One)

There is an [all-in-one compose file](./docker/compose.yaml) which contains all DIM components.

### Components

- pdns-int: powerdns instance for internal zones
- pdns-pub: powerdns instance for external zones
- pdns-rec: required for powerdns_auth
- mysql_db: we have 3 databases in here
  - dim: for dim component
  - pdns_int for pdns-int
  - pdns_out for pdns-out
- dim: dim core component
- dim-nginx: nginx proxy to proxypass different paths to different components. [Config file](./docker/conf/dim-nginx.conf)
- pdns-output: Write dim zones and changes to powerdns databases
- openldap: openldap server for dim
- phpldapadmin: good to have some gui for ldap
- ndcli: dim cli client

to setup docker deployment:

```sh
cd docker
docker compose up -d
```

Init dim database

```sh
docker compose exec -it dim bash
./manage_db init
exit
```

Before set a user to role `Admin` you need to login to create user in dim database
Use ndcli to login

```sh
docker compose exec ndcli ndcli login --username john1 --password P@ssw0rd
```

To set an user's role to `Admin`

```sh
docker compose exec -it dim bash
./manage_dim set_user -u john1 -t Admin
```

***Notes:***

- There is no data presistance, you should modify docker compose for your needs
- The default listen url is <http://dim-nginx:8000> and you have to add this url to your /etc/hosts file to reach service from localhost.
- Default docker network is on 10.10.0.0/16 and for required communication every containers have a assigned ip address in docker compose

### Default users

You can add new users with openldap or use below users to interact with dim

| Username| Password |
| ---     | ---      |
| john3   | P@ssw0rd |
| john2   | P@ssw0rd |
| john1   | P@ssw0rd |
