# ITOUDP-8041: "show rr" and "modify rr" for A/AAAA/PTR must accept a
# layer3domain like "create rr" and "delete rr" do, including the default from
# NDCLI_LAYER3DOMAIN / layer3domain= in ~/.ndclirc.
$ ndcli create layer3domain two type vrf rd 0:2
$ ndcli create zone a.de
WARNING - Creating zone a.de without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli create rr a.de. a 10.0.0.1 layer3domain default
INFO - Marked IP 10.0.0.1 from layer3domain default as static
INFO - Creating RR @ A 10.0.0.1 in zone a.de
INFO - No zone found for 1.0.0.10.in-addr.arpa.
WARNING - No reverse zone found. Only creating forward entry.
$ ndcli create rr b.a.de. a 10.0.0.1 layer3domain two --allow-overlap
WARNING - 10.0.0.1 in layer3domain two overlaps with 10.0.0.1 in layer3domain default
INFO - Marked IP 10.0.0.1 from layer3domain two as static
INFO - Creating RR b A 10.0.0.1 in zone a.de
INFO - No zone found for 1.0.0.10.in-addr.arpa.
WARNING - No reverse zone found. Only creating forward entry.

# 10.0.0.1 exists in two layer3domains: without one the address is ambiguous,
# exactly as for "delete rr"
$ ndcli modify rr a.de. a 10.0.0.1 --ttl 60
ERROR - A layer3domain is needed
$ ndcli show rr a.de. a 10.0.0.1
ERROR - A layer3domain is needed

$ ndcli modify rr a.de. a 10.0.0.1 layer3domain default --ttl 60
$ ndcli show rr a.de. a 10.0.0.1 layer3domain default
created:2026-10-13 12:00:00
created_by:admin
modified:2026-10-13 12:00:00
modified_by:admin
rr:@ 60 A 10.0.0.1
ttl:60
zone:a.de

$ ndcli modify rr b.a.de. a 10.0.0.1 layer3domain two -c "in two"
comment:in two
created:2026-10-13 12:00:00
created_by:admin
modified:2026-10-13 12:00:00
modified_by:admin
rr:b A 10.0.0.1
zone:a.de

# the layer3domain must exist
$ ndcli modify rr a.de. a 10.0.0.1 layer3domain nosuch --ttl 60
ERROR - Layer3domain 'nosuch' does not exist

# the reporter's case: layer3domain only set in the environment / ~/.ndclirc
# (shell lines can not carry expected output in this harness, so check with show)
$ NDCLI_CONFIG=/dev/null NDCLI_LAYER3DOMAIN=default ndcli modify rr a.de. a 10.0.0.1 --ttl 120
$ ndcli show rr a.de. a 10.0.0.1 layer3domain default
created:2026-10-13 12:00:00
created_by:admin
modified:2026-10-13 12:00:00
modified_by:admin
rr:@ 120 A 10.0.0.1
ttl:120
zone:a.de

# addresses that exist only once still need no layer3domain
$ ndcli create rr c.a.de. a 10.0.0.3 layer3domain default
INFO - Marked IP 10.0.0.3 from layer3domain default as static
INFO - Creating RR c A 10.0.0.3 in zone a.de
INFO - No zone found for 3.0.0.10.in-addr.arpa.
WARNING - No reverse zone found. Only creating forward entry.
$ ndcli modify rr c.a.de. a 10.0.0.3 --ttl 300
$ ndcli show rr c.a.de. a 10.0.0.3
created:2026-10-13 12:00:00
created_by:admin
modified:2026-10-13 12:00:00
modified_by:admin
rr:c 300 A 10.0.0.3
ttl:300
zone:a.de
