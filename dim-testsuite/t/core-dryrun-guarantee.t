$ ndcli create zone dryrun-test.com -n
INFO - Dryrun mode, no data will be modified
WARNING - Creating zone dryrun-test.com without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli list zone dryrun-test.com
ERROR - Zone dryrun-test.com does not exist

$ ndcli create zone dryrun-test.com -q
$ ndcli create rr www.dryrun-test.com. A 10.88.1.1 -n
INFO - Dryrun mode, no data will be modified
INFO - Marked IP 10.88.1.1 from layer3domain default as static
INFO - Creating RR www A 10.88.1.1 in zone dryrun-test.com
INFO - No zone found for 1.1.88.10.in-addr.arpa.
WARNING - No reverse zone found. Only creating forward entry.

$ ndcli list rrs www.dryrun-test.com.
INFO - Result for list rrs www.dryrun-test.com.
record zone view ttl type value
