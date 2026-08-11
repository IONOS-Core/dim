$ ndcli create container 0.0.0.0/0 layer3domain default
INFO - Creating container 0.0.0.0/0 in layer3domain default
$ ndcli show container 0.0.0.0/0 layer3domain default
created:2024-06-18 11:02:43
ip:0.0.0.0/0
layer3domain:default
modified:2024-06-18 11:02:43
modified_by:admin
status:Container
$ ndcli list containers 0.0.0.0/0
layer3domain: default
0.0.0.0/0 (Container)
  0.0.0.0/0 (Available)
$ ndcli create container 0.0.0.0/0 layer3domain default 
ERROR - 0.0.0.0/0 already exists in layer3domain default with status Container

$ ndcli create container ::/0 layer3domain default
INFO - Creating container ::/0 in layer3domain default

$ ndcli show container ::/0 layer3domain default
created:2024-06-18 11:02:43
ip:::/0
layer3domain:default
modified:2024-06-18 11:02:43
modified_by:admin
status:Container

$ ndcli list containers ::/0
layer3domain: default
::/0 (Container)
  ::/0 (Available)
$ ndcli create container ::/0 layer3domain default
ERROR - ::/0 already exists in layer3domain default with status Container

$ ndcli list containers layer3domain default
layer3domain: default
0.0.0.0/0 (Container)
  0.0.0.0/0 (Available)
::/0 (Container)
  ::/0 (Available)
