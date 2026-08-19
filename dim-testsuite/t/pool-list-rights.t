$ ndcli create pool some-pool
$ ndcli create container 10.0.0.0/8
INFO - Creating container 10.0.0.0/8 in layer3domain default
$ ndcli modify pool some-pool add subnet 10.0.0.0/24
INFO - Created subnet 10.0.0.0/24 in layer3domain default
WARNING - Creating zone 0.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli list pool some-pool rights
action object group

$ ndcli modify user-group all_users grant allocate some-pool
$ ndcli create user-group testgroup
$ ndcli modify user-group testgroup grant allocate some-pool

$ ndcli list pool some-pool rights
action   object    group
allocate some-pool all_users
allocate some-pool testgroup

# Test granting and revoking attr rights (dot-suffix and prefix symmetry)
$ ndcli modify user-group testgroup grant attr audit some-pool
$ ndcli modify user-group testgroup grant attr audit. some-pool
$ ndcli list pool some-pool rights
action      object    group
allocate    some-pool all_users
allocate    some-pool testgroup
attr.audit  some-pool testgroup
attr.audit. some-pool testgroup

# Revoke using the short name without prefix or trailing dot
$ ndcli modify user-group testgroup revoke attr audit some-pool
$ ndcli list pool some-pool rights
action      object    group
allocate    some-pool all_users
allocate    some-pool testgroup
attr.audit. some-pool testgroup

# Revoke using the full right name including dot
$ ndcli modify user-group testgroup revoke attr attr.audit. some-pool
$ ndcli list pool some-pool rights
action   object    group
allocate some-pool all_users
allocate some-pool testgroup

# Test our second fix: warning/error on remove attrs with colon
$ ndcli modify pool some-pool set attrs mykey:myvalue
$ ndcli modify pool some-pool remove attrs mykey:myvalue
ERROR - Attribute name 'mykey:myvalue' contains ':'. Did you mean to specify only the attribute name?

$ ndcli modify pool some-pool remove subnet 10.0.0.0/24
INFO - Deleting zone 0.0.10.in-addr.arpa
$ ndcli delete pool some-pool
$ ndcli delete container 10.0.0.0/8
INFO - Deleting container 10.0.0.0/8 from layer3domain default
