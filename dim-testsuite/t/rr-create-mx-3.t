# https://github.com/ionos-core/dim/issues/128
#
# RFC7505
# " ... To indicate that a domain does not accept email, it advertises a single
# MX RR (see Section 3.3.9 of [RFC1035]) with an RDATA section consisting of
# preference number 0 and a zero-length label, written in master files as ".",
# as the exchange domain, to denote that there exists no mail exchanger for a
# domain. ... "

# A NULL MX record is handled like a CNAME. There can only be one.

$ ndcli create container 1.0.0.0/8
INFO - Creating container 1.0.0.0/8 in layer3domain default
$ ndcli create pool apool
$ ndcli modify pool apool add subnet 1.2.3.0/24 gw 1.2.3.1
INFO - Created subnet 1.2.3.0/24 in layer3domain default
WARNING - Creating zone 3.2.1.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli create zone a.de mail hostmaster@a.de
WARNING - Creating zone a.de without profile
WARNING - Primary NS for this Domain is now localhost.

# Try to create a MX pointing to a name outside of dim -> give warning
$ ndcli create rr a.de. mx 10 mx.outside.zone.
INFO - Creating RR @ MX 10 mx.outside.zone. in zone a.de
WARNING - mx.outside.zone. does not exist.

# Try to create a NULL MX -> must fail
$ ndcli create rr a.de. mx 0 .
ERROR - Can not create NULL MX record - other MX record already exists

$ ndcli delete rr a.de. mx -q

$ ndcli create rr a.de. mx 0 .
INFO - Creating RR @ MX 0 . in zone a.de
WARNING - . does not exist.

# Try to create a MX in parallel to a NULL MX -> fail
$ ndcli create rr a.de. mx 10 mx.outside.zone.
ERROR - Can not create MX record - NULL MX record already exists

$ ndcli delete zone a.de --cleanup -q
$ ndcli delete zone 3.2.1.in-addr.arpa --cleanup
$ ndcli modify pool apool remove subnet 1.2.3.0/24
$ ndcli delete pool apool
$ ndcli delete container 1.0.0.0/8
INFO - Deleting container 1.0.0.0/8 from layer3domain default
