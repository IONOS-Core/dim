$ ndcli create container 198.51.100.0/24 -q
$ ndcli create pool anycast-service-pool -q
$ ndcli modify pool anycast-service-pool add subnet 198.51.100.53/32
ERROR - Creating Subnet blocks with only one IP addresss is not allowed
