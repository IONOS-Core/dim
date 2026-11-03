$ ndcli create zone a.com
WARNING - Creating zone a.com without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli show zone a.com
created:2015-10-27 14:01:03
created_by:admin
modified:2015-10-27 14:01:03
modified_by:admin
name:a.com
views:1
$ ndcli modify zone a.com dnssec enable 8 ksk 2048 zsk 1024
Created key a.com_ksk_20151105_113739 for zone a.com
Created key a.com_zsk_20151105_113740 for zone a.com
$ ndcli show zone a.com
created:2015-10-27 14:01:03
created_by:admin
default_algorithm:8
default_ksk_bits:2048
default_zsk_bits:1024
modified:2015-10-27 14:01:03
modified_by:admin
name:a.com
views:1
$ ndcli show zone a.com
created:2015-10-27 14:01:03
created_by:admin
default_algorithm:8
default_ksk_bits:2048
default_zsk_bits:1024
modified:2015-10-27 14:01:03
modified_by:admin
name:a.com
views:1
$ ndcli list zone a.com keys
label                     type tag   algorithm bits created
a.com_ksk_20151105_114613 KSK  11111 8         2048 2015-11-05 11:46:13
a.com_zsk_20151105_114613 ZSK  11111 8         1024 2015-11-05 11:46:13
$ ndcli modify zone a.com set attrs default_algorithm:rsasha1
ERROR - Algorithm can only be 8 (rsasha256)
$ ndcli modify zone a.com set attrs default_ksk_bits:4096
$ ndcli modify zone a.com set attrs default_zsk_bits:4096
$ ndcli modify zone a.com set attrs default_ksk_bits:a
ERROR - Invalid key length: "a"
$ ndcli create output output plugin pdns-db db-uri mysql://pdns:pdns@127.0.0.1:3307/pdns_slave
$ ndcli create zone-group zg
$ ndcli modify zone-group zg add zone a.com
$ ndcli modify output output add zone-group zg
$ ndcli modify zone a.com set attrs dnssec_validity_window:345599
ERROR - Invalid dnssec validity window: 345599
$ ndcli modify zone a.com set attrs dnssec_validity_window:345600
$ ndcli show zone a.com
created:2015-10-27 14:01:03
created_by:admin
default_algorithm:8
default_ksk_bits:4096
default_zsk_bits:4096
dnssec_validity_window:345600
modified:2015-10-27 14:01:03
modified_by:admin
name:a.com
views:1
zone_groups:1
$ ndcli modify zone a.com set attrs dnssec_validity_skew:-1
ERROR - Invalid dnssec validity skew: -1
$ ndcli modify zone a.com set attrs dnssec_validity_skew:abc
ERROR - Invalid dnssec validity skew: "abc"
$ ndcli modify zone a.com set attrs dnssec_validity_skew:1800
WARNING - Setting dnssec_validity_skew to less than 3600 seconds (1 hour) is not recommended (see RFC 6781)
$ ndcli modify zone a.com set attrs dnssec_validity_skew:7200
$ ndcli show zone a.com
created:2015-10-27 14:01:03
created_by:admin
default_algorithm:8
default_ksk_bits:4096
default_zsk_bits:4096
dnssec_validity_skew:7200
dnssec_validity_window:345600
modified:2015-10-27 14:01:03
modified_by:admin
name:a.com
views:1
zone_groups:1
