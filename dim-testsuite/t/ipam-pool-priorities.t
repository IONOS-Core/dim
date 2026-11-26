$ ndcli create container 192.168.250.0/23 -q
$ ndcli create pool prio-eval-pool -q
$ ndcli modify pool prio-eval-pool add subnet 192.168.250.0/30 -q
$ ndcli modify pool prio-eval-pool add subnet 192.168.251.0/30 -q
$ ndcli modify pool prio-eval-pool subnet 192.168.251.0/30 set prio 1 -q
$ ndcli modify pool prio-eval-pool subnet 192.168.250.0/30 set prio 2 -q
$ ndcli create zone prio-eval.com -q

$ ndcli create rr h1.prio-eval.com. from prio-eval-pool -q
created:2026-11-20 12:00:00
ip:192.168.251.1
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:prio-eval-pool
ptr_target:h1.prio-eval.com.
reverse_zone:251.168.192.in-addr.arpa
status:Static
subnet:192.168.251.0/30

$ ndcli create rr h2.prio-eval.com. from prio-eval-pool -q
created:2026-11-20 12:00:00
ip:192.168.251.2
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:prio-eval-pool
ptr_target:h2.prio-eval.com.
reverse_zone:251.168.192.in-addr.arpa
status:Static
subnet:192.168.251.0/30

$ ndcli create rr h3.prio-eval.com. from prio-eval-pool -q
created:2026-11-20 12:00:00
ip:192.168.250.1
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:prio-eval-pool
ptr_target:h3.prio-eval.com.
reverse_zone:250.168.192.in-addr.arpa
status:Static
subnet:192.168.250.0/30

$ ndcli delete rr h1.prio-eval.com. a -q
$ ndcli create rr h4.prio-eval.com. from prio-eval-pool -q
created:2026-11-20 12:00:00
ip:192.168.251.1
layer3domain:default
mask:255.255.255.252
modified:2026-11-20 12:00:00
modified_by:admin
pool:prio-eval-pool
ptr_target:h4.prio-eval.com.
reverse_zone:251.168.192.in-addr.arpa
status:Static
subnet:192.168.251.0/30
