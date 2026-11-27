$ ndcli create user-group sec-audit-team -q
$ ndcli create pool finance-data-pool -q
$ ndcli modify user-group sec-audit-team grant attr audit. finance-data-pool -q
$ ndcli list pool finance-data-pool rights
action      object            group
attr.audit. finance-data-pool sec-audit-team

$ ndcli modify user-group sec-audit-team revoke attr attr.audit. finance-data-pool -q
$ ndcli list pool finance-data-pool rights
action object group
