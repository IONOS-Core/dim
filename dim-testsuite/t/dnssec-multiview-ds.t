$ ndcli create zone mv-ds-parent.com -q
$ ndcli modify zone mv-ds-parent.com create view internal -q
$ ndcli modify zone mv-ds-parent.com create view external -q
$ ndcli modify zone mv-ds-parent.com dnssec enable 8 ksk 2048 zsk 1024 -q

$ ndcli create zone sub.mv-ds-parent.com -q
$ ndcli modify zone sub.mv-ds-parent.com dnssec enable 8 ksk 2048 zsk 1024 -q

$ ndcli list rrs sub.mv-ds-parent.com. DS
INFO - Result for list rrs sub.mv-ds-parent.com.
record zone             view     ttl type value
sub    mv-ds-parent.com default      DS   7520 8 2 962D36C8ED6E6E9DFFE04337295FC8826EF9673B287780929BEA7FA79BA0B709
sub    mv-ds-parent.com external     DS   7520 8 2 962D36C8ED6E6E9DFFE04337295FC8826EF9673B287780929BEA7FA79BA0B709
sub    mv-ds-parent.com internal     DS   7520 8 2 962D36C8ED6E6E9DFFE04337295FC8826EF9673B287780929BEA7FA79BA0B709

$ ndcli modify zone sub.mv-ds-parent.com dnssec disable -q

$ ndcli list rrs sub.mv-ds-parent.com. DS
INFO - Result for list rrs sub.mv-ds-parent.com.
record zone view ttl type value
