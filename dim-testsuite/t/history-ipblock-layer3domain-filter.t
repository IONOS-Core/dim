# "history ipblock CIDR layer3domain X" shows only the history of X; without a
# layer3domain the history of all layer3domains is shown.
$ ndcli create layer3domain two type vrf rd 0:2
$ ndcli create container 10.0.0.0/8 layer3domain default
INFO - Creating container 10.0.0.0/8 in layer3domain default
$ ndcli create container 10.0.0.0/8 layer3domain two
INFO - Creating container 10.0.0.0/8 in layer3domain two

$ ndcli history ipblock 10.0.0.0/8 layer3domain two
timestamp                  user  tool   originating_ip objclass name       action
2019-01-15 11:06:07.659774 admin native 127.0.0.1      ipblock  10.0.0.0/8 created in layer3domain two

$ ndcli history ipblock 10.0.0.0/8 layer3domain default
timestamp                  user  tool   originating_ip objclass name       action
2019-01-15 11:06:07.509940 admin native 127.0.0.1      ipblock  10.0.0.0/8 created in layer3domain default

$ ndcli history ipblock 10.0.0.0/8
timestamp                  user  tool   originating_ip objclass name       action
2019-01-15 11:06:07.659774 admin native 127.0.0.1      ipblock  10.0.0.0/8 created in layer3domain two
2019-01-15 11:06:07.509940 admin native 127.0.0.1      ipblock  10.0.0.0/8 created in layer3domain default
