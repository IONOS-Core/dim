# Tutorial

## `ndcli`

This name roots back to NetDot CLI. We never managed to change it after the creation of DIM.

Use `man ndcli` for complete list of commands.

Use `-h` or autocompletion via Tab key to see the valid set of subcommands

Use `-d` to see API calls made.

Use `-H` to get a parsing friendly output.

To limit memory usage on the server machine many commands limit the result set. Please use `-L` to override.

## Setup templates

### Setup DNS Zone Profiles

Every DNS Zone you create needs some basic NS and MX records and settings in the SOA adjusted to your needs. To avoid typing this every time lets create zone-profiles.

For a small setup which only has internal and public zones, two zone-profiles are recommended.

create zone-profile public

```
ndcli create zone-profile public
ndcli modify zone-profile public create rr @ NS ns1.example.com.
ndcli modify zone-profile public create rr @ NS ns2.example.com.
ndcli modify zone-profile public set ttl 86400
ndcli modify zone-profile public set primary ns1.example.com.
ndcli modify zone-profile public set mail dnadmins@example.com
ndcli modify zone-profile public set minimum 600
ndcli modify zone-profile public set refresh 3600
ndcli modify zone-profile public set expire 2592000
```

create zone-profile internal

```
ndcli create zone-profile internal
ndcli modify zone-profile internal create rr @ NS ins1.internal.test.
ndcli modify zone-profile internal create rr @ NS ins2.internal.test.
ndcli modify zone-profile internal set primary ins1.internal.test.
ndcli modify zone-profile internal set mail dnsadmins@example.com
ndcli modify zone-profile internal set minimum 60
ndcli modify zone-profile internal set ttl 86400
```

create zones

```
ndcli create zone example.com profile public
ndcli create zone internal.test profile internal
ndcli create zone 10.in-addr.arpa profile internal
```

Map zones to zone-groups to PowerDNS databases.

```
ndcli create zone-group internal
ndcli create zone-group public

ndcli modify zone-group internal add zone internal\.test
ndcli modify zone-group internal add zone 10.in-addr.arpa
ndcli modify zone-group internal add zone example\.com
ndcli modify zone-group public add zone example\.com

ndcli create output pdns-int plugin pdns-db db-uri mysql://dim_pdns_int_user:SuperSecret1@127.0.0.1:3306/pdns_int
ndcli create output pdns-pub plugin pdns-db db-uri mysql://dim_pdns_pub_user:SuperSecret2@127.0.0.1:3306/pdns_pub

ndcli modify output pdns-int add zone-group internal
ndcli modify output pdns-pub add zone-group public
```

This looks complicated but allows you to have the same zone in multiple pdns dbs.

Verify that DNS resolution is now working

```
$ dig NS internal.test @127.1.0.1

; <<>> DiG 9.11.26-RedHat-9.11.26-4.el8_4 <<>> NS internal.test @127.1.0.1
;; global options: +cmd
;; Got answer:
;; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 3217
;; flags: qr aa rd; QUERY: 1, ANSWER: 2, AUTHORITY: 0, ADDITIONAL: 1
;; WARNING: recursion requested but not available

;; OPT PSEUDOSECTION:
; EDNS: version: 0, flags:; udp: 1220
;; QUESTION SECTION:
;internal.test.   IN NS

;; ANSWER SECTION:
internal.test.  86400 IN NS ins1.internal.test.
internal.test.  86400 IN NS ins2.internal.test.

;; Query time: 2 msec
;; SERVER: 127.1.0.1#53(127.1.0.1)
;; WHEN: Tue Jun 22 17:31:44 CEST 2021
;; MSG SIZE  rcvd: 80
```

### Setup your IP Space - Containers

You manage your IP-Space in DIM with the following objects: `containers`, `pools` and `subnets`. For a minimal layer 2 Management you can tag your pools with `vlan` information.

For an enterprise environment where you have several v4 /16 and v6 /32 aggregates to manage, you typically add these directly as containers.

All IP objects are uniquely identified by prefix, length and layer3domain. It is not possible to have a /24 container and a /24 subnet of the same prefix. In this case please create two /25 subnets.

In a home user environment I recommend to just add rfc1918 v4 and 2000::/3 v6
containers:

```
ndcli create container 10.0.0.0/8 origin:rfc1918 reverse_dns_profile:internal
ndcli create container 192.168.0.0/16 origin:rfc1918 reverse_dns_profile:internal
ndcli create container 172.16.0.0/12 origin:rfc1918 reverse_dns_profile:internal
ndcli create container 100.64.0.0/10 origin:rfc6598 reverse_dns_profile:internal
ndcli create container 2000::/3 origin:rfc4291 reverse_dns_profile:public
ndcli create container 2001:db8::/32 origin:rfc3849 "comment:Documentation Prefix"
ndcli create container fc00::/7 origin:rfc4193 "comment:Unique Local Unicast" reverse_dns_profile:internal
ndcli create container fe80::/10 origin:rfc4291 "comment:Link-Scoped Unicast"
ndcli create container ff00::/8 origin:rfc4291 "comment:Multicast"
ndcli create container 9.0.0.0/8 origin:IBM-DEMO "comment:IBM - DEMO only"
```

### Setup your IP Space - IP Pools (v4)

IP Pools help you as an abstraction between prefixes and consumers. The consumers receive the pool name and
use the api to allocate and free ip addresses.
If the pool runs out of free ip addresses, the network team can add another prefix to the pool and the
consumers do not need to change anything.

create ip pools

```sh
ndcli create container 10.10.0.0/16 "comment:Data Center Networks"
ndcli create container 10.10.0.0/20 "comment:Database Servers Networks"
ndcli create pool de-fuh-bar-pg-600 vlan 600
ndcli modify pool de-fuh-bar-pg-600 add subnet 10.10.0.0/28 gw 10.10.0.1
```

The reverse zone `0.10.10.in-addr.arpa` was automatically created. It was automatically added to all zone-groups where the next less specific zone resides.

```sh
dig SOA 0.10.10.in-addr.arpa @127.1.0.1
```

just works.

Give the router ip a DNS name `ndcli create rr v600.net.example.com. a 10.10.0.1`.

```sh
dig v600.net.example.com @127.1.0.1
dig -x 10.10.0.1 @127.1.0.1
```

also just works

### using IP-Pools (v4)

The ip-pool can now be used like this:

```
ndcli create rr db1234.db.internal.test. from de-fuh-bar-pg-600
INFO - Marked IP 10.10.0.2 from layer3domain default as static
INFO - Creating RR db1234.db A 10.10.0.2 in zone internal.test
INFO - Creating RR 2 PTR db1234.db.internal.test. in zone 0.10.10.in-addr.arpa
created:2021-06-22 17:43:53.546471
gateway:10.10.0.1
ip:10.10.0.2
layer3domain:default
mask:255.255.255.240
modified:2021-06-22 17:43:53.546509
modified_by:user
pool:de-fuh-bar-pg-600
ptr_target:db1234.db.internal.test.
reverse_zone:0.10.10.in-addr.arpa
status:Static
subnet:10.10.0.0/28
```

The user gets the DNS entries and all information needed for OS installation. And the new name start resolving practically immediately.

### using only IP functionality of IP-Pools (v4)

For the cases where you need to reserve an IP Address but you do not know the DNS Name you can allocate/free IPs like this

```
ndcli modify pool de-fuh-bar-pg-600 get ip comment:"this is a test for the Tutorial" ticket:your-ticket-id whatever:"you want"
comment:this is a test for the Tutorial
created:2021-06-28 12:23:15.761298
gateway:10.10.0.1
ip:10.10.0.3
layer3domain:default
mask:255.255.255.240
modified:2021-06-28 12:23:15.806782
modified_by:user
pool:de-fuh-bar-pg-600
reverse_zone:0.10.10.in-addr.arpa
status:Static
subnet:10.10.0.0/28
ticket:your-ticket-id
whatever:you want
```

DIM allows free form porperties on pools and ips.

Freeing an IP works like this:

```
ndcli modify pool de-fuh-bar-pg-600 free ip 10.10.0.3
```

If the ip is used in a DNS entry, the DNS entry is also deleted. Use `-n` for a dryrun.

### Setup your IP Space - IP Pools (v6)

The  demo VM already has `2001:db8::/32` configured. Use `ndcli list containers 2000::/3`to see what is there.

### using IP-Pools (v6)

```
ndcli create zone 8.b.d.0.1.0.0.2.in-addr.arpa profile public
ndcli modify zone-group internal add zone 8\.b\.d\.0\.1\.0\.0\.2\.in-addr\.arpa
ndcli modify zone-group public add zone 8\.b\.d\.0\.1\.0\.0\.2\.in-addr\.arpa
```

Setup a large space where prefixes can be allocated to hand out

```
ndcli create container 2001:db8:c00::/38
ndcli create pool de-fuh-bar-room-1_v6
ndcli modify pool de-fuh-bar-room-1_v6 add subnet 2001:db8:c10::/44
INFO - Created subnet 2001:db8:c10::/44 in layer3domain default
INFO - Creating zone 1.c.0.8.b.d.0.1.0.0.2.ip6.arpa with profile public
```

Now products can allocate for example /64 or /56 like this

```
ndcli modify pool de-fuh-bar-room-1_v6 get delegation 64
created:2021-06-28 17:02:13.079736
ip:2001:db8:c10:1::/64
layer3domain:default
modified:2021-06-28 17:02:13.079760
modified_by:user
pool:de-fuh-bar-room-1_v6
prefixlength:44
reverse_zone:1.c.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:c10::/44

ndcli modify pool de-fuh-bar-room-1_v6 get delegation 56
created:2021-06-28 17:02:34.763005
ip:2001:db8:c10:100::/56
layer3domain:default
modified:2021-06-28 17:02:34.763024
modified_by:user
pool:de-fuh-bar-room-1_v6
prefixlength:44
reverse_zone:1.c.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:c10::/44
```

To see how many more delegations can be allocated in the pool, set `assignmentsize` to change the output format

```
ndcli modify pool de-fuh-bar-room-1_v6 set attrs assignmentsize:56

ndcli list pool de-fuh-bar-room-1_v6
prio subnet            gateway free(/56) total(/56)
   1 2001:db8:c10::/44              4094       4096
INFO - Total free IPs: 4094
```

Network- and Sysadmkins probably use DIM for v6 more like this:

```
ndcli create pool de-fuh-bar-infra_v6

ndcli modify pool de-fuh-bar-infra_v6 add subnet 2001:db8:c00::/64 gw 2001:db8:c00::1
INFO - Created subnet 2001:db8:c00::/64 in layer3domain default
INFO - Creating zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa with profile public

ndcli modify pool de-fuh-bar-infra_v6 mark delegation 2001:db8:c00::/112 -f
created:2021-06-28 17:10:44.119599
gateway:2001:db8:c00::1
ip:2001:db8:c00::/112
layer3domain:default
modified:2021-06-28 17:10:44.119623
modified_by:user
pool:de-fuh-bar-infra_v6
prefixlength:64
reverse_zone:0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Delegation
subnet:2001:db8:c00::/64

ndcli create rr infra.net.example.com. aaaa 2001:db8:c00::1
INFO - Marked IP 2001:db8:c00::1 from layer3domain default as static
INFO - Creating RR infra.net AAAA 2001:db8:c00::1 in zone example.com
INFO - Creating RR 1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0 PTR infra.net.example.com. in zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa

ndcli create rr infra-a.net.example.com. aaaa 2001:db8:c00::a
INFO - Marked IP 2001:db8:c00::a from layer3domain default as static
INFO - Creating RR infra-a.net AAAA 2001:db8:c00::a in zone example.com
INFO - Creating RR a.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0 PTR infra-a.net.example.com. in zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa

ndcli create rr infra-b.net.example.com. aaaa 2001:db8:c00::b
INFO - Marked IP 2001:db8:c00::b from layer3domain default as static
INFO - Creating RR infra-b.net AAAA 2001:db8:c00::b in zone example.com
INFO - Creating RR b.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0 PTR infra-b.net.example.com. in zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
```

Now the network guys have their IPs in the first /112. A sysadmin typing

```
ndcli create rr coolv6service.internal.test. from de-fuh-bar-infra_v6
INFO - Marked IP 2001:db8:c00::1:0 from layer3domain default as static
INFO - Creating RR coolv6service AAAA 2001:db8:c00::1:0 in zone internal.test
INFO - Creating RR 0.0.0.0.1.0.0.0.0.0.0.0.0.0.0.0 PTR coolv6service.internal.test. in zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
created:2021-06-28 17:18:01.548342
gateway:2001:db8:c00::1
ip:2001:db8:c00::1:0
layer3domain:default
modified:2021-06-28 17:18:01.548376
modified_by:user
pool:de-fuh-bar-infra_v6
prefixlength:64
ptr_target:coolv6service.internal.test.
reverse_zone:0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Static
subnet:2001:db8:c00::/64
```

gets the first free ip after the initial /112 in the /64.

Because the IPv6 address room is so huge DIM can automaticaly hide ips. This can be enabled by setting the attribute `allocation_strategy:random`. This works for single ips and prefixes.

```
ndcli modify pool de-fuh-bar-infra_v6 set attrs allocation_strategy:random
ndcli create rr coolv7service.internal.test. from de-fuh-bar-infra_v6
INFO - Marked IP 2001:db8:c00:0:5367:ce8a:d648:3c01 from layer3domain default as static
INFO - Creating RR coolv7service AAAA 2001:db8:c00:0:5367:ce8a:d648:3c01 in zone internal.test
INFO - Creating RR 1.0.c.3.8.4.6.d.a.8.e.c.7.6.3.5 PTR coolv7service.internal.test. in zone 0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
created:2021-06-28 17:21:49.298789
gateway:2001:db8:c00::1
ip:2001:db8:c00:0:5367:ce8a:d648:3c01
layer3domain:default
modified:2021-06-28 17:21:49.298813
modified_by:user
pool:de-fuh-bar-infra_v6
prefixlength:64
ptr_target:coolv7service.internal.test.
reverse_zone:0.0.0.0.0.0.c.0.8.b.d.0.1.0.0.2.ip6.arpa
status:Static
subnet:2001:db8:c00::/64
```

## list vlans

```
ndcli list vlans
vlan pools
     de-fuh-bar-infra_v6 de-fuh-bar-room-1_v6
 600 de-fuh-bar-pg-600
```

## finding pools

### list by vlan id

```
ndcli list pools 600
INFO - Result for list pools 600
name              vlan subnets
de-fuh-bar-pg-600 600  10.10.4.0/24 10.10.0.0/24
```

### list by pool name pattern

```
ndcli list pools \*pg\*
INFO - Result for list pools *pg*
name              vlan subnets
de-fuh-bar-pg-600 600  10.10.4.0/24 10.10.0.0/24
```

### list pools having at least one subnet from a prefix

```
ndcli list pools 10.10.0.0/23
INFO - Result for list pools 10.10.0.0/23
name              vlan subnets
de-fuh-bar-pg-600 600  10.10.4.0/24 10.10.0.0/24
```

### Pool life cycle

To see if pool runs out of space use:

```
ndcli list pool de-fuh-bar-pg-600
prio subnet       gateway   free total
   1 10.10.0.0/28 10.10.0.1   11    16
INFO - Total free IPs: 11
```

add a subnet

```
ndcli modify pool de-fuh-bar-pg-600 add subnet 10.10.4.0/24 gw 10.10.4.1
INFO - Created subnet 10.10.4.0/24 in layer3domain default
INFO - Creating zone 4.10.10.in-addr.arpa with profile internal
INFO - Creating views default for zone 4.10.10.in-addr.arpa
```

change subnet priority so that new allocations are done with the /24

```
ndcli modify pool de-fuh-bar-pg-600 subnet 10\.10\.4\.0/24 set prio 1
ndcli list pool de-fuh-bar-pg-600
prio subnet       gateway   free total
   2 10.10.0.0/28 10.10.0.1   11    16
   1 10.10.4.0/24 10.10.4.1  254   256
INFO - Total free IPs: 265
```

alternatively you can directly change the /28 to a /24 like this

```
ndcli modify pool de-fuh-bar-pg-600 remove subnet 10.10.0.0/28 -f
ndcli modify pool de-fuh-bar-pg-600 add subnet 10.10.0.0/24 gw 10.10.0.1
```

`--force` without `--cleanup` just removes the subnet definition, all DNS and used IPs stays untouched.

### DNS Zone management

#### import forward zone

RRSIG etc are automatically discarded

```sh
dig axfr iks-jena.de @avalon.iks-jena.de. | ndcli import zone iks-jena.de
ndcli modify zone-group public add zone iks-jena.de
```

#### modify NS and SOA records

```
ndcli list rrs iks-jena.de NS
INFO - Result for list rrs iks-jena.de
record zone        view    ttl type value
@      iks-jena.de default     NS   avalon.iks-jena.de.
@      iks-jena.de default     NS   broceliande.iks-jena.de.

ndcli create rr iks-jena.de. NS ns1.example.com.
ndcli create rr iks-jena.de. NS ns2.example.com.
ndcli delete rr iks-jena.de. NS avalon.iks-jena.de.
ndcli delete rr iks-jena.de. NS broceliande.iks-jena.de.

ndcli modify zone iks-jena\.de set mail dnsadmin@example.com primary ns1.example.com. minimum 600
```

#### import reverse zone

see #84

```
broken as of 2021-06-30
```

#### "Most specific zone"

DIM has the idea to put DNS records in the most specific zones. DIM takes care of automatically rearranging records.

Lets create `net.example.com`

```
ndcli create zone net.example.com profile public
INFO - Creating zone net.example.com with profile public
INFO - Creating views default for zone net.example.com
INFO - Moving RR v600.net A 10.10.0.1 in zone net.example.com from zone example.com
INFO - Moving RR infra.net AAAA 2001:db8:c00::1 in zone net.example.com from zone example.com
INFO - Moving RR infra-a.net AAAA 2001:db8:c00::a in zone net.example.com from zone example.com
INFO - Moving RR infra-b.net AAAA 2001:db8:c00::b in zone net.example.com from zone example.com
INFO - Creating RR net NS ns1.example.com. in zone example.com
WARNING - ns1.example.com. does not exist.
WARNING - The name net.example.com. already existed, creating round robin record
INFO - Creating RR net NS ns2.example.com. in zone example.com
WARNING - ns2.example.com. does not exist.
```

Records are automatically moved to the new zone and delegating records are added to the parent zone.

More options are available, please see help. The records moved experience a little downtime, please be careful.

Delegating records can be modified like this:

```sh
ndcli modify zone example.com create rr net ns some.thing.
```

#### DNS Views

DIM can manage different contents for the same zone, called views. A typical usecase is that a zone contains more
records for internal use than visible to the public.

```
ndcli create zone views.test profile internal

ndcli modify zone views.test create view public profile public

ndcli modify zone views.test rename view default to internal

ndcli create rr internal-db.views.test. a 10.10.0.21 view internal

ndcli create rr www.views.test. a 9.1.1.2 view internal public

ndcli list zone views.test view internal
record      zone       ttl   type value
@           views.test 86400 SOA  ins1.internal.test. dnsadmins.example.com.
                                  2021070603 14400 3600 605000 60
@           views.test       NS   ins1.internal.test.
@           views.test       NS   ins2.internal.test.
internal-db views.test       A    10.10.0.21
www         views.test       A    9.1.1.2

ndcli list zone views.test view public
record zone       ttl   type value
@      views.test 86400 SOA  ns1.example.com. dnadmins.example.com. 2021070602
                             3600 3600 2592000 600
@      views.test       NS   ns1.example.com.
@      views.test       NS   ns2.example.com.
www    views.test       A    9.1.1.2
```

Now the zone-views need to go in the right zone-group to get visible.

```sh
ndcli modify zone-group internal add zone views.test view internal
ndcli modify zone-group public add zone views.test view public
```

Please use `dig` to check. E.g. for internal

```sh
dig internal-db.views.test @127.1.0.1
```

for public

```sh
dig internal-db.views.test @127.2.0.1
```

#### DNSSec

DIM makes it easy to use DNSSec. Switch on DNSSec for example.com (remember: there is tab completion and -h)

```sh
ndcli modify zone example\.com dnssec enable 8 ksk 4096 zsk 2048 nsec3 7 salted
```

(Sign Zone example.com with algorithm 8 (RSA/SHA-256) 4096 bit key signing key, 2048 bit zone signing key, with NSEC3 7 iterations, salted. Salt is generated automatically.)

Check with dig

```sh
dig +dnssec example.com @127.2.0.1
```

DNSSec parameters can be seen with `ndcli show zone example.com`. You can modify these parameters with `ndcli modify zone Z set attr P:V`.

To see DS records::

```sh
ndcli list zone example.com ds
```

##### Example ZSK key roll

```sh
ndcli modify zone example\.com dnssec new zsk
```

Wait at minimum 24h.

Identify and delete old ZSK

```sh
ndcli list zone example.com keys
ndcli modify zone example\.com dnssec delete key example.com_zsk_20180827_132749335059
```

KSK roll works identical.

##### setting/updating DS with AutoDNS3 API at registry

If your domain is registered at internetX you can use the AutoDNS3 API adapter to update DS records::

```sh
ndcli create registrar-account ad3-example.com plugin autodns3 url http://autodns.com user USER password PASSWORD subaccount SUBACCOUNT
ndcli modify registrar-account ad3-example\.com add example\.com
ndcli list zone example\.com registrar-actions
```

Run `/opt/dim/bin/manage_dim autodns3` to process the registrar actions. Creating a systemd-unit file to start this daemon at boot time is left as an exercise to the reader. In `/etc/dim/dim-autodns3-plugin.cfg` you can set a http proxy to connect to autodns3 url.

At the moment the only DS records can be set. If this works for us we plan to implement functions to update delegations, create and delete domains.

##### SOME BIG FAT WARNINGS

Read RFC4641 and/or RFC6781.

You must run `/opt/dim/bin/manage_dim update_validity` every day to make sure that RRSIGs are kept up to date.

You must create a cron job that regularly checks your signed zones for validity. Check `dnssec_mon script on github.

## User Management

DIM does not implement password store. If it is configured to use LDAP, it simply checks that the provided username/password can login to the provided LDAP configuration.

DIM does implement a authorization system.

DIM comes with a preconfigured admin user. It should be deleted in a live system. As there is no `ndcli delete user` you need to login to the MySQL Database and use SQL.

A user with `user_type_id` in `user` table set to 1 is a super-admin. No restrictions apply for this user. This value is only read when a user session is created, so if you change a user_type_id, the user needs to login again.

Normal users have `user_type_id` 3. 2 is unused.

Entries in the user table are created after the first successful login. With `/opt/dim/bin/manage_dim ldap_sync` you can sync all users in configured `LDAP_URL` into DIM.

With ndcli rights in DIM can only be granted to user groups not to individual users.

DIM has a network admin and a DNS admin role.

Network admins can create/modify/delete containers and pools. Network admins can grant the allocate right on pools.

DNS admins can create/modify/delete zones, zone-groups and outputs. DNS admins can grant create_rr, delete_rr and zone_admin on zones and zone-create to user-groups.

Both types of admins can create user-groups.

In our organization we DNS admins create user-groups prefixed with 'DNS' and grant DNS related rights o them. Network admins have unprefixed user-groups and grant rights to them.

## Audit logs

DIM keeps history of all data changing transactions in the system. Yon can query the history records with ndcli:

```
ndcli history -h
Usage: ndcli history [<subcommand>]

Subcommands:
 [any]                any
 ipblock              ipblock
 layer3domain         layer3domain
 output               output
 outputs              outputs
 pool                 pool
 registrar-account    registrar-account
 rr                   rr
 rrs                  rrs
 user                 user
 user-group           user-group
 user-groups          user-groups
 zone                 zone
 zone-group           zone-group
 zone-groups          zone-groups
 zone-profile         zone-profile
 zone-profiles        zone-profiles
 zones                zones

 Options for ndcli history:
  -b --begin              begin timestamp (default: None)
  -e --end                end timestamp (default: None)
  -L --limit              max number of results (default: 10)
  -H --script             scripting mode output (no headers, tab between fields)

 Options for ndcli:
  -d --debug              also print DEBUG messages
  -D --detailed           detailed return codes
  -h --help               display usage information
  -p --password           Dim password
  -q --quiet              don't print WARNING or INFO messages
  -s --server             Dim server URL
  -u --username           Dim username
  -V --version            print version and exit
  -w --warnings           don't print INFO messages
```

## Multiple RFC1918 IP Spaces / Layer3domains

TBD

## Proxied user authentication

See developer Guide for this feature that allows easy integration with other tools.

## History

Back in 2010-2011 we started using [https://github.com/cvicente/Netdot](Netdot) but it
was in perl, there was no cli, it was not focused enough on DNS. So @abenea
rewrote the core functionallity needed for us "over the weekend" in python 2 back in 2011.

DIM was designed in a ~10000 Person hosting company to support 100s of products with IPv4 and IPv6 Addresses
and help ~400 technical people to store and retrieve accurat technical information.
