# Test ALIAS record creation and functionality
# Based on draft-ietf-dnsop-aname-04 specification

$ ndcli create zone example.com
WARNING - Creating zone example.com without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli create zone target.com
WARNING - Creating zone target.com without profile
WARNING - Primary NS for this Domain is now localhost.

# Create target A record for ALIAS to point to
$ ndcli create rr host.target.com. a 192.168.1.100 -q

# Test basic ALIAS record creation at zone apex
$ ndcli create rr example.com. alias host.target.com.
INFO - Creating RR @ ALIAS host.target.com. in zone example.com

# Test ALIAS record creation at subdomain
$ ndcli create rr www.example.com. alias host.target.com.
INFO - Creating RR www ALIAS host.target.com. in zone example.com

# Test that ALIAS can coexist with other records (unlike CNAME)
$ ndcli create rr example.com. txt "v=spf1 include:_spf.example.com ~all" -q
$ ndcli create rr example.com. mx 10 mail.example.com. -q

# List zone to verify ALIAS records
$ ndcli list zone example.com
record zone        ttl   type  value
@      example.com 86400 SOA   localhost. hostmaster.example.com. 2012111402 14400 3600 605000 86400
@      example.com       ALIAS host.target.com.
@      example.com       TXT   "v=spf1 include:_spf.example.com ~all"
@      example.com       MX    10 mail.example.com.
www    example.com       ALIAS host.target.com.

# Test that ALIAS cannot coexist with CNAME
$ ndcli create rr test.example.com. cname host.target.com. -q
$ ndcli create rr test.example.com. alias host.target.com.
ERROR - test.example.com. ALIAS host.target.com. cannot be created because a CNAME with the same name exists

# Test that CNAME cannot coexist with ALIAS
$ ndcli create rr alias-test.example.com. alias host.target.com. -q
$ ndcli create rr alias-test.example.com. cname host.target.com.
ERROR - alias-test.example.com. CNAME host.target.com. cannot be created because other RRs with the same name or target exist

# Test DNSSEC constraints - ALIAS records should not be allowed in DNSSEC-enabled zones
$ ndcli create zone dnssec-test.com
WARNING - Creating zone dnssec-test.com without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli modify zone dnssec-test.com dnssec enable 8 ksk 2048 zsk 1024
Created key dnssec-test.com_ksk_20151105_113739 for zone dnssec-test.com
Created key dnssec-test.com_zsk_20151105_113740 for zone dnssec-test.com

$ ndcli create rr dnssec-test.com. alias host.target.com.
ERROR - ALIAS records are not supported in DNSSEC-enabled zones

# Test that DNSSEC cannot be enabled on zones with ALIAS records
$ ndcli create zone alias-zone.com
WARNING - Creating zone alias-zone.com without profile
WARNING - Primary NS for this Domain is now localhost.

$ ndcli create rr alias-zone.com. alias host.target.com. -q

$ ndcli modify zone alias-zone.com dnssec enable 8 ksk 2048 zsk 1024
ERROR - DNSSEC cannot be enabled on zones containing ALIAS records

# Test ALIAS record validation - target must be FQDN
$ ndcli create rr invalid.example.com. alias invalid-target
ERROR - Invalid target: invalid-target

# Test ALIAS record deletion
$ ndcli delete rr www.example.com. alias -q
$ ndcli delete rr example.com. alias -q

# Test that multiple ALIAS records with same name are not allowed (like CNAME)
$ ndcli create rr multi.example.com. alias host.target.com. -q
$ ndcli create rr multi.example.com. alias other.target.com.
ERROR - multi.example.com. ALIAS other.target.com. cannot be created because other RRs with the same name exist

# Cleanup
$ ndcli delete zone example.com --cleanup
INFO - Deleting RR @ TXT "v=spf1 include:_spf.example.com ~all" from zone example.com
INFO - Deleting RR @ MX 10 mail.example.com. from zone example.com
INFO - Deleting RR alias-test ALIAS host.target.com. from zone example.com
INFO - Deleting RR multi ALIAS host.target.com. from zone example.com
INFO - Deleting RR test CNAME host.target.com. from zone example.com

$ ndcli delete zone dnssec-test.com --cleanup

$ ndcli delete zone alias-zone.com --cleanup
INFO - Deleting RR @ ALIAS host.target.com. from zone alias-zone.com

$ ndcli delete zone target.com --cleanup
INFO - Deleting RR host A 192.168.1.100 from zone target.com
INFO - Freeing IP 192.168.1.100 from layer3domain default
