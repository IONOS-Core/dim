$ ndcli create zone serial-limit-test.com -q
$ ndcli modify zone serial-limit-test.com set serial 4294967295

$ ndcli create rr node1.serial-limit-test.com. A 10.1.1.1 --only-forward
ERROR - Invalid serial: 4294967296
