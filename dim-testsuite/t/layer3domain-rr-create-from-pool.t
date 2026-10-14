# ITOUDP-8009: "create rr ... from POOL" must only look at the layer3domain of
# POOL when it checks whether the allocated address is already in use. The same
# address being Static in another layer3domain is not a conflict.
$ ndcli create layer3domain two type vrf rd 15143:1

$ ndcli create container 10.0.0.0/8 layer3domain default
INFO - Creating container 10.0.0.0/8 in layer3domain default
$ ndcli create container 10.0.0.0/8 layer3domain two
INFO - Creating container 10.0.0.0/8 in layer3domain two

$ ndcli create pool p layer3domain default
$ ndcli create pool p2 layer3domain two

$ ndcli modify pool p add subnet 10.0.0.0/24
INFO - Created subnet 10.0.0.0/24 in layer3domain default
WARNING - Creating zone 0.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli modify pool p2 add subnet 10.0.0.0/24 --allow-overlap
INFO - Created subnet 10.0.0.0/24 in layer3domain two
WARNING - 10.0.0.0/24 in layer3domain two overlaps with 10.0.0.0/24 in layer3domain default
INFO - Creating view two in zone 0.0.10.in-addr.arpa without profile

$ ndcli create zone a.de
WARNING - Creating zone a.de without profile
WARNING - Primary NS for this Domain is now localhost.

# 10.0.0.1 is now Static in layer3domain two ...
$ ndcli create rr other.a.de. from p2
INFO - Marked IP 10.0.0.1 from layer3domain two as static
INFO - Creating RR other A 10.0.0.1 in zone a.de
INFO - Creating RR 1 PTR other.a.de. in zone 0.0.10.in-addr.arpa view two
created:2026-10-13 12:00:00
ip:10.0.0.1
layer3domain:two
mask:255.255.255.0
modified:2026-10-13 12:00:00
modified_by:admin
pool:p2
ptr_target:other.a.de.
reverse_zone:0.0.10.in-addr.arpa
status:Static
subnet:10.0.0.0/24

# ... and still the first free address of pool p in layer3domain default
$ ndcli create rr a.de. from p
INFO - Marked IP 10.0.0.1 from layer3domain default as static
INFO - Creating RR @ A 10.0.0.1 in zone a.de
INFO - Creating RR 1 PTR a.de. in zone 0.0.10.in-addr.arpa view default
created:2026-10-13 12:00:00
ip:10.0.0.1
layer3domain:default
mask:255.255.255.0
modified:2026-10-13 12:00:00
modified_by:admin
pool:p
ptr_target:a.de.
reverse_zone:0.0.10.in-addr.arpa
status:Static
subnet:10.0.0.0/24

# "get ip" from a pool already behaved like this
$ ndcli modify pool p2 get ip
created:2026-10-13 12:00:00
ip:10.0.0.2
layer3domain:two
mask:255.255.255.0
modified:2026-10-13 12:00:00
modified_by:admin
pool:p2
reverse_zone:0.0.10.in-addr.arpa
status:Static
subnet:10.0.0.0/24
$ ndcli create rr b.a.de. from p
INFO - Marked IP 10.0.0.2 from layer3domain default as static
INFO - Creating RR b A 10.0.0.2 in zone a.de
INFO - Creating RR 2 PTR b.a.de. in zone 0.0.10.in-addr.arpa view default
created:2026-10-13 12:00:00
ip:10.0.0.2
layer3domain:default
mask:255.255.255.0
modified:2026-10-13 12:00:00
modified_by:admin
pool:p
ptr_target:b.a.de.
reverse_zone:0.0.10.in-addr.arpa
status:Static
subnet:10.0.0.0/24
