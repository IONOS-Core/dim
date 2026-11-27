$ ndcli login -u user -p p
$ ndcli create zone sec-admin-only.com -q
$ ndcli create zone team-alpha-shared.com -q

$ ndcli create rr hack.sec-admin-only.com. A 10.1.1.1 -u user
ERROR - Permission denied (can_create_rr zone sec-admin-only.com)

$ ndcli create user-group team-alpha -q
$ ndcli modify user-group team-alpha add user user -q
$ ndcli modify user-group team-alpha grant create_rr team-alpha-shared.com -q

$ ndcli create rr app.team-alpha-shared.com. A 10.20.1.1 -u user -q

$ ndcli create rr exploit.sec-admin-only.com. A 10.20.1.1 -u user
ERROR - Permission denied (can_create_rr zone sec-admin-only.com)

$ ndcli delete rr app.team-alpha-shared.com. a -q

$ ndcli modify user-group team-alpha revoke create_rr team-alpha-shared.com -q
$ ndcli create rr app.team-alpha-shared.com. A 10.20.1.1 -u user
ERROR - Permission denied (can_create_rr zone team-alpha-shared.com)
