$ ndcli create container 10.0.0.0/8 layer3domain default -q
$ ndcli create layer3domain vrf-blue type vrf rd 100:1 -q
$ ndcli create layer3domain vrf-green type vrf rd 100:2 -q
$ ndcli create container 10.0.0.0/8 layer3domain vrf-blue -q
$ ndcli create container 10.0.0.0/8 layer3domain vrf-green -q

$ ndcli create pool pool-blue layer3domain vrf-blue -q
$ ndcli create pool pool-green layer3domain vrf-green -q

$ ndcli modify pool pool-blue add subnet 10.50.1.0/24 -q
$ ndcli modify pool pool-green add subnet 10.50.1.0/24 --allow-overlap -q

$ ndcli modify pool pool-blue add subnet 10.50.1.128/25
ERROR - 10.50.1.128/25 in layer3domain vrf-blue would overlap with 10.50.1.0/24 in layer3domain vrf-green

$ ndcli create zone vrf-test.com -q
$ ndcli create rr h-blue.vrf-test.com. A 10.50.1.42 layer3domain vrf-blue -q
$ ndcli create rr h-green.vrf-test.com. A 10.50.1.42 layer3domain vrf-green -q
$ ndcli show ip 10.50.1.42 layer3domain vrf-blue
created:2026-11-20 12:00:00
ip:10.50.1.42
layer3domain:vrf-blue
mask:255.255.255.0
modified:2026-11-20 12:00:00
modified_by:admin
pool:pool-blue
ptr_target:h-blue.vrf-test.com.
reverse_zone:1.50.10.in-addr.arpa
status:Static
subnet:10.50.1.0/24
$ ndcli show ip 10.50.1.42 layer3domain vrf-green
created:2026-11-20 12:00:00
ip:10.50.1.42
layer3domain:vrf-green
mask:255.255.255.0
modified:2026-11-20 12:00:00
modified_by:admin
pool:pool-green
ptr_target:h-green.vrf-test.com.
reverse_zone:1.50.10.in-addr.arpa
status:Static
subnet:10.50.1.0/24
