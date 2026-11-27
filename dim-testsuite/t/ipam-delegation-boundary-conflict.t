$ ndcli create container 192.168.0.0/16 -q
$ ndcli create pool boundary-bug-pool -q
$ ndcli modify pool boundary-bug-pool add subnet 192.168.230.0/24 -q
$ ndcli modify pool boundary-bug-pool mark delegation 192.168.230.128/25
ERROR - 192.168.230.128/25 from layer3domain default has children

$ ndcli modify pool boundary-bug-pool mark delegation 192.168.230.128/25 --force
created:2026-11-20 12:00:00
ip:192.168.230.128/25
layer3domain:default
mask:255.255.255.0
modified:2026-11-20 12:00:00
modified_by:admin
pool:boundary-bug-pool
reverse_zone:230.168.192.in-addr.arpa
status:Delegation
subnet:192.168.230.0/24
