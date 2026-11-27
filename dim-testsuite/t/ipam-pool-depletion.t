$ ndcli create container 192.168.188.0/24 -q
$ ndcli create pool depletion-pool -q
$ ndcli modify pool depletion-pool add subnet 192.168.188.0/30 -q
$ ndcli create zone depletion-test.com -q
$ ndcli create rr host1.depletion-test.com. from depletion-pool -q
created:2026-11-20 12:00:00
ip:192.168.188.1
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:depletion-pool
ptr_target:host1.depletion-test.com.
reverse_zone:188.168.192.in-addr.arpa
status:Static
subnet:192.168.188.0/30

$ ndcli create rr host2.depletion-test.com. from depletion-pool -q
created:2026-11-20 12:00:00
ip:192.168.188.2
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:depletion-pool
ptr_target:host2.depletion-test.com.
reverse_zone:188.168.192.in-addr.arpa
status:Static
subnet:192.168.188.0/30

$ ndcli create rr host3.depletion-test.com. from depletion-pool
ERROR - No free IP address found

$ ndcli delete rr host1.depletion-test.com. a -q
$ ndcli create rr host3.depletion-test.com. from depletion-pool -q
created:2026-11-20 12:00:00
ip:192.168.188.1
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:depletion-pool
ptr_target:host3.depletion-test.com.
reverse_zone:188.168.192.in-addr.arpa
status:Static
subnet:192.168.188.0/30
