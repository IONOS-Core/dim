# ITOUDP-6303: with -n ndcli sends dryrun to every call, also to read-only
# ones like ippool_get_subnets, which used to fail with
# "unexpected keyword argument 'dryrun'".
$ ndcli create container 10.0.0.0/8 layer3domain default
INFO - Creating container 10.0.0.0/8 in layer3domain default
$ ndcli create pool p layer3domain default

$ ndcli delete pool p -f -n
INFO - Dryrun mode, no data will be modified

# nothing was deleted
$ ndcli list pools p
INFO - Result for list pools p
name vlan subnets layer3domain
p                 default

$ ndcli modify pool p add subnet 10.0.0.0/24
INFO - Created subnet 10.0.0.0/24 in layer3domain default
WARNING - Creating zone 0.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli delete pool p -n
INFO - Dryrun mode, no data will be modified
ERROR - You cannot delete pool because it contains 1 subnet(s). However, you can use --force option to delete it.

$ ndcli delete pool p
ERROR - You cannot delete pool because it contains 1 subnet(s). However, you can use --force option to delete it.
