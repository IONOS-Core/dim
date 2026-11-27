$ ndcli create zone view-rename-test.com -q
$ ndcli modify zone view-rename-test.com rename view default to internal -q

$ ndcli list zone view-rename-test.com views
name
internal

$ ndcli list zone view-rename-test.com view default
ERROR - Zone view-rename-test.com has no view named default.

$ ndcli modify zone view-rename-test.com create view external -q

$ ndcli modify zone view-rename-test.com rename view internal to external
ERROR - A zone view named 'external' already exists for the zone view-rename-test.com

$ ndcli modify zone view-rename-test.com rename view ghost to phantom
ERROR - Zone view-rename-test.com has no view named ghost.
