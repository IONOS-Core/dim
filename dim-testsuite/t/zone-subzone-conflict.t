$ ndcli create zone conflict-parent.com -q
$ ndcli modify zone conflict-parent.com create view internal -q
$ ndcli modify zone conflict-parent.com create view external -q

$ ndcli create rr node1.sub.conflict-parent.com. A 10.0.0.1 view internal --only-forward -q
$ ndcli create rr node1.sub.conflict-parent.com. A 1.2.3.4 view external --only-forward -q

$ ndcli create zone sub.conflict-parent.com
WARNING - Creating zone sub.conflict-parent.com without profile
WARNING - Primary NS for this Domain is now localhost.
INFO - Creating views default, internal, external for zone sub.conflict-parent.com
INFO - Moving RR node1.sub A 10.0.0.1 in zone sub.conflict-parent.com view internal from zone conflict-parent.com view internal
INFO - Moving RR node1.sub A 1.2.3.4 in zone sub.conflict-parent.com view external from zone conflict-parent.com view external

$ ndcli list zone sub.conflict-parent.com views
name
default
external
internal
