# Test NetworkAdmin users can create pools with overlapping subnets in different layer3domains
# Make sure the user exists
# as user netadmin
$ ndcli login -u netadmin -p p

# as user admin
# Create NetworkAdmin user
$ ndcli create user-group networkadmins
$ ndcli modify user-group networkadmins add user netadmin
$ ndcli modify user-group networkadmins grant network_admin

# Create two layer3domains
$ ndcli create layer3domain domain1 type vrf rd 0:1
$ ndcli create layer3domain domain2 type vrf rd 0:2

# Create containers in both domains to support the pools
$ ndcli create container 10.0.0.0/8 layer3domain domain1
INFO - Creating container 10.0.0.0/8 in layer3domain domain1
$ ndcli create container 10.0.0.0/8 layer3domain domain2
INFO - Creating container 10.0.0.0/8 in layer3domain domain2

# Test: NetworkAdmin should be able to create pools in different layer3domains
$ ndcli create pool pool1 layer3domain domain1 -u netadmin
$ ndcli create pool pool2 layer3domain domain2 -u netadmin

# Test: NetworkAdmin should be able to add overlapping subnets with --allow-overlap
$ ndcli modify pool pool1 add subnet 10.0.1.0/24 -u netadmin
INFO - Created subnet 10.0.1.0/24 in layer3domain domain1
WARNING - Creating zone 1.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli modify pool pool2 add subnet 10.0.1.0/24 --allow-overlap -u netadmin
INFO - Created subnet 10.0.1.0/24 in layer3domain domain2
WARNING - 10.0.1.0/24 in layer3domain domain2 overlaps with 10.0.1.0/24 in layer3domain domain1
INFO - Creating view domain2 in zone 1.0.10.in-addr.arpa without profile

# Test: Should work without --allow-overlap if subnets don't overlap
$ ndcli modify pool pool1 add subnet 10.0.2.0/24 -u netadmin
INFO - Created subnet 10.0.2.0/24 in layer3domain domain1
WARNING - Creating zone 2.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli modify pool pool2 add subnet 10.0.3.0/24 -u netadmin
INFO - Created subnet 10.0.3.0/24 in layer3domain domain2
WARNING - Creating zone 3.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

# Test: Pool operations should work for NetworkAdmin
$ ndcli list pools -u netadmin
name  vlan subnets                 layer3domain
pool1      10.0.1.0/24 10.0.2.0/24 domain1
pool2      10.0.1.0/24 10.0.3.0/24 domain2

$ ndcli list pool pool1 subnets -u netadmin
INFO - Total free IPs: 508
prio subnet      gateway free total
   1 10.0.1.0/24          254   256
   2 10.0.2.0/24          254   256

$ ndcli list pool pool2 subnets -u netadmin
INFO - Total free IPs: 508
prio subnet      gateway free total
   1 10.0.1.0/24          254   256
   2 10.0.3.0/24          254   256            

# Clean up
$ ndcli modify pool pool1 remove subnet 10.0.1.0/24 -f
$ ndcli modify pool pool1 remove subnet 10.0.2.0/24 -f
$ ndcli modify pool pool2 remove subnet 10.0.1.0/24 -f
$ ndcli modify pool pool2 remove subnet 10.0.3.0/24 -f
$ ndcli delete pool pool1
$ ndcli delete pool pool2
$ ndcli delete container 10.0.0.0/8 layer3domain domain1
INFO - Deleting container 10.0.0.0/8 from layer3domain domain1
$ ndcli delete container 10.0.0.0/8 layer3domain domain2
INFO - Deleting container 10.0.0.0/8 from layer3domain domain2
$ ndcli delete layer3domain domain1
$ ndcli delete layer3domain domain2
$ ndcli delete user-group networkadmins