$ ndcli create zone old.example.com
WARNING - Creating zone old.example.com without profile
WARNING - Primary NS for this Domain is now localhost.
$ ndcli create zone new.example.com
WARNING - Creating zone new.example.com without profile
WARNING - Primary NS for this Domain is now localhost.

# Test creating a basic DNAME record
$ ndcli create rr dept.old.example.com. dname dept.new.example.com. -q

# Test creating DNAME with TTL and comment
$ ndcli create rr sales.old.example.com. ttl 1800 dname sales.new.example.com. --comment "Sales department redirect" -q

# Test DNAME cannot be created at zone apex (RFC 6672 requirement)
$ ndcli create rr old.example.com. dname new.example.com.
ERROR - It is not allowed to create a DNAME for a zone

# Test DNAME conflicts with other records at same name
$ ndcli create rr conflict.old.example.com. a 1.2.3.4 -q
$ ndcli create rr conflict.old.example.com. dname conflict.new.example.com.
ERROR - conflict.old.example.com. DNAME conflict.new.example.com. cannot be created because other RRs with the same name exist

# Test other records cannot be created at same name as DNAME
$ ndcli create rr dept.old.example.com. a 1.2.3.5
ERROR - dept.old.example.com. A 1.2.3.5 cannot be created because a DNAME with the same name exists

# Test records cannot be created under DNAME subtree
$ ndcli create rr hr.dept.old.example.com. a 1.2.3.6
ERROR - hr.dept.old.example.com. A 1.2.3.6 cannot be created under DNAME subtree dept.old.example.com.

# Test DNAME cannot be created if records exist under the subtree
$ ndcli create rr marketing.test.old.example.com. a 1.2.3.7 -q
$ ndcli create rr test.old.example.com. dname test.new.example.com.
ERROR - test.old.example.com. DNAME test.new.example.com. cannot be created because RRs exist under the DNAME subtree

# Clean up the conflicting record and create the DNAME
$ ndcli delete rr marketing.test.old.example.com. a -q
$ ndcli create rr test.old.example.com. dname test.new.example.com. -q

# Test multiple DNAME records can coexist if they don't conflict
$ ndcli create rr finance.old.example.com. dname finance.new.example.com. -q

# Test showing DNAME records
$ ndcli show rr dept.old.example.com. dname
created:2012-11-14 11:03:02
created_by:user
modified:2012-11-14 11:03:02
modified_by:user
rr:dept DNAME dept.new.example.com.
zone:old.example.com

# Test showing DNAME record with comment
$ ndcli show rr sales.old.example.com. dname
comment:Sales department redirect
created:2012-11-14 11:03:02
created_by:user
modified:2012-11-14 11:03:02
modified_by:user
rr:sales 1800 DNAME sales.new.example.com.
ttl:1800
zone:old.example.com

# Test listing zone with DNAME records
$ ndcli list zone old.example.com
record     zone           ttl   type  value
@          old.example.com 86400 SOA   localhost. hostmaster.old.example.com. 2012111402 14400 3600 605000 86400
conflict   old.example.com       A     1.2.3.4
dept       old.example.com       DNAME dept.new.example.com.
finance    old.example.com       DNAME finance.new.example.com.
sales      old.example.com 1800  DNAME sales.new.example.com.
test       old.example.com       DNAME test.new.example.com.

# Test deleting DNAME records
$ ndcli delete rr sales.old.example.com. dname sales.new.example.com. -q

# Test deleting DNAME by name only
$ ndcli delete rr finance.old.example.com. dname -q

# Verify records were deleted
$ ndcli list zone old.example.com
record   zone           ttl   type  value
@        old.example.com 86400 SOA   localhost. hostmaster.old.example.com. 2012111403 14400 3600 605000 86400
conflict old.example.com       A     1.2.3.4
dept     old.example.com       DNAME dept.new.example.com.
test     old.example.com       DNAME test.new.example.com.

# Test that records can be created under DNAME subtree after DNAME is deleted
$ ndcli delete rr dept.old.example.com. dname -q
$ ndcli create rr hr.dept.old.example.com. a 1.2.3.8 -q

# Cleanup
$ ndcli delete zone old.example.com --cleanup
INFO - Deleting RR conflict A 1.2.3.4 from zone old.example.com
INFO - Freeing IP 1.2.3.4 from layer3domain default
INFO - Deleting RR hr.dept A 1.2.3.8 from zone old.example.com
INFO - Freeing IP 1.2.3.8 from layer3domain default
INFO - Deleting RR test DNAME test.new.example.com. from zone old.example.com
$ ndcli delete zone new.example.com --cleanup