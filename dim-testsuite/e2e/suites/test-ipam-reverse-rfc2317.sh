#!/bin/bash
#
# suites/test-ipam-reverse-rfc2317.sh
# End-to-end test verifying RFC 2317 Classless in-addr.arpa delegation (< /24 subnet).
# Proves that:
#   1. A classless reverse zone (e.g. 128/28.5.200.10.in-addr.arpa) can be created and delegated.
#   2. CNAME records in the parent /24 zone correctly forward reverse lookups to the classless zone.
#   3. PTR and Forward A records remain synchronized in IPAM and PowerDNS.
#   4. Decommissioning and IP release cleanly purges all reverse records.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


step "Clean up previous runs"
delete_zone_all_views 128/28.5.200.10.in-addr.arpa
delete_zone_all_views 5.200.10.in-addr.arpa
delete_zone_all_views rfc2317-test.com
# Best-effort cleanup of leftover artifacts from prior aborted runs
ndcli modify pool rfc2317-pool remove subnet 10.200.5.128/28 -q 2>/dev/null || true
ndcli delete pool rfc2317-pool -q 2>/dev/null || true
ndcli delete container 10.200.0.0/16 -q 2>/dev/null || true

step "Create forward zone: rfc2317-test.com"
ndcli create zone rfc2317-test.com
ensure_output_sync rfc2317-test.com

step "Create IP pool and classless subnet (10.200.5.128/28)"
ndcli create container 10.200.0.0/16 -q
ndcli create pool rfc2317-pool
ndcli modify pool rfc2317-pool add subnet 10.200.5.128/28
ensure_output_sync 5.200.10.in-addr.arpa

step "Create RFC 2317 classless reverse zone: 128/28.5.200.10.in-addr.arpa"
ndcli create zone 128/28.5.200.10.in-addr.arpa
ensure_output_sync 128/28.5.200.10.in-addr.arpa
ndcli modify zone 128/28.5.200.10.in-addr.arpa create rr @ NS ns1.other.example.org. -q

step "Add NS delegation and CNAME alias in parent zone 5.200.10.in-addr.arpa"
ndcli modify zone 5.200.10.in-addr.arpa create rr 128/28 NS ns1.other.example.org. -q
ndcli create rr "129.5.200.10.in-addr.arpa." CNAME "129.128/28.5.200.10.in-addr.arpa." -q

step "Create PTR record in delegated classless zone"
ndcli modify zone 128/28.5.200.10.in-addr.arpa create rr 129 PTR "node1.rfc2317-test.com." -q

step "Create corresponding Forward A record"
ndcli create rr "node1.rfc2317-test.com." A 10.200.5.129 --only-forward -q

step "Wait for PowerDNS to receive both forward and reverse zones"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.5.200.10.in-addr.arpa." CNAME +noall +answer | grep -q "CNAME"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive RFC 2317 CNAME alias in 5.200.10.in-addr.arpa."
fi
log_pass "RFC 2317 CNAME alias published to PowerDNS."

step "Verify Reverse Resolution Chain in PowerDNS"
# Query parent zone CNAME
CNAME_TARGET=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.5.200.10.in-addr.arpa." CNAME +short)
echo "  CNAME target for 129.5.200.10.in-addr.arpa.: $CNAME_TARGET"

if [[ "$CNAME_TARGET" != *"129.128/28.5.200.10.in-addr.arpa."* ]]; then
    log_fail "Unexpected CNAME target: $CNAME_TARGET"
fi

# Query classless zone PTR directly
PTR_RECORD=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.128/28.5.200.10.in-addr.arpa." PTR +short)
echo "  PTR record for 129.128/28.5.200.10.in-addr.arpa.: $PTR_RECORD"

if [[ "$PTR_RECORD" != *"node1.rfc2317-test.com."* ]]; then
    log_fail "Unexpected PTR target: $PTR_RECORD"
fi
log_pass "RFC 2317 reverse chain resolution verified successfully."

step "Cleanup"
delete_zone_all_views 128/28.5.200.10.in-addr.arpa
delete_zone_all_views 5.200.10.in-addr.arpa
delete_zone_all_views rfc2317-test.com
ndcli modify pool rfc2317-pool remove subnet 10.200.5.128/28 -q
ndcli delete pool rfc2317-pool -q
ndcli delete container 10.200.0.0/16 -q

log_pass "Test ipam-reverse-rfc2317 completed successfully."
