# ITOUDP-5166: "list containers layer3domain X" without a container argument
# must show the containers of X, also when X is not "default".
$ ndcli create layer3domain two type vrf rd 0:2

$ ndcli create container 10.0.0.0/8 layer3domain default
INFO - Creating container 10.0.0.0/8 in layer3domain default
$ ndcli create container 10.0.16.64/28 layer3domain two
INFO - Creating container 10.0.16.64/28 in layer3domain two
$ ndcli create pool p2 layer3domain two
$ ndcli modify pool p2 add subnet 10.0.16.64/31 --allow-overlap
INFO - Created subnet 10.0.16.64/31 in layer3domain two
WARNING - Creating zone 16.0.10.in-addr.arpa without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli list containers layer3domain two 10.0.16.64/28
layer3domain: two
10.0.16.64/28 (Container)
  10.0.16.64/31 (Subnet) pool:p2
  10.0.16.66/31 (Available)
  10.0.16.68/30 (Available)
  10.0.16.72/29 (Available)

$ ndcli list containers layer3domain two
layer3domain: two
10.0.16.64/28 (Container)
  10.0.16.64/31 (Subnet) pool:p2
  10.0.16.66/31 (Available)
  10.0.16.68/30 (Available)
  10.0.16.72/29 (Available)

$ ndcli list containers layer3domain default
layer3domain: default
10.0.0.0/8 (Container)
  10.0.0.0/8 (Available)

# a container that was moved from another layer3domain must not keep a parent
# from its old layer3domain, otherwise it vanishes from the top level listing
$ ndcli create layer3domain three type vrf rd 0:3
$ ndcli create container 10.0.32.0/24 layer3domain default
INFO - Creating container 10.0.32.0/24 in layer3domain default
$ ndcli modify container 10.0.32.0/24 layer3domain default move to three
$ ndcli list containers layer3domain three
layer3domain: three
10.0.32.0/24 (Container)
  10.0.32.0/24 (Available)
$ ndcli list containers layer3domain default
layer3domain: default
10.0.0.0/8 (Container)
  10.0.0.0/8 (Available)
