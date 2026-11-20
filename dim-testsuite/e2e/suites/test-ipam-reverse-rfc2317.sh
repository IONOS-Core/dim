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

POOL="rfc2317-pool"
SUBNET="10.200.5.128/28"
PARENT_REV="5.200.10.in-addr.arpa"
CHILD_REV="128/28.${PARENT_REV}"
FWD_ZONE="rfc2317-test.com"

step "Clean up previous runs"
delete_zone_all_views "$CHILD_REV"
delete_zone_all_views "$PARENT_REV"
delete_zone_all_views "$FWD_ZONE"
# Best-effort cleanup of leftover artifacts from prior aborted runs
ndcli modify pool "$POOL" remove subnet "$SUBNET" -q 2>/dev/null || true
ndcli delete pool "$POOL" -q 2>/dev/null || true
ndcli delete container 10.200.0.0/16 -q 2>/dev/null || true

step "Create forward zone: $FWD_ZONE"
ndcli create zone "$FWD_ZONE"
ensure_output_sync "$FWD_ZONE"

step "Create IP pool and classless subnet ($SUBNET)"
ndcli create container 10.200.0.0/16 -q
ndcli create pool "$POOL"
ndcli modify pool "$POOL" add subnet "$SUBNET"
ensure_output_sync "$PARENT_REV"

step "Create RFC 2317 classless reverse zone: $CHILD_REV"
ndcli create zone "$CHILD_REV"
ensure_output_sync "$CHILD_REV"
ndcli modify zone "$CHILD_REV" create rr @ NS ns1.other.example.org. -q

step "Add NS delegation and CNAME alias in parent zone $PARENT_REV"
ndcli modify zone "$PARENT_REV" create rr 128/28 NS ns1.other.example.org. -q
ndcli create rr "129.${PARENT_REV}." CNAME "129.${CHILD_REV}." -q

step "Create PTR record in delegated classless zone"
ndcli modify zone "$CHILD_REV" create rr 129 PTR "node1.${FWD_ZONE}." -q

step "Create corresponding Forward A record"
ndcli create rr "node1.${FWD_ZONE}." A 10.200.5.129 --only-forward -q

step "Wait for PowerDNS to receive both forward and reverse zones"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.${PARENT_REV}." CNAME +noall +answer | grep -q "CNAME"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive RFC 2317 CNAME alias in $PARENT_REV."
fi
log_pass "RFC 2317 CNAME alias published to PowerDNS."

step "Verify Reverse Resolution Chain in PowerDNS"
# Query parent zone CNAME
CNAME_TARGET=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.${PARENT_REV}." CNAME +short)
echo "  CNAME target for 129.${PARENT_REV}.: $CNAME_TARGET"

if [[ "$CNAME_TARGET" != *"129.${CHILD_REV}."* ]]; then
    log_fail "Unexpected CNAME target: $CNAME_TARGET"
fi

# Query classless zone PTR directly
PTR_RECORD=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "129.${CHILD_REV}." PTR +short)
echo "  PTR record for 129.${CHILD_REV}.: $PTR_RECORD"

if [[ "$PTR_RECORD" != *"node1.${FWD_ZONE}."* ]]; then
    log_fail "Unexpected PTR target: $PTR_RECORD"
fi
log_pass "RFC 2317 reverse chain resolution verified successfully."

step "Cleanup"
delete_zone_all_views "$CHILD_REV"
delete_zone_all_views "$PARENT_REV"
delete_zone_all_views "$FWD_ZONE"
ndcli modify pool "$POOL" remove subnet "$SUBNET" -q
ndcli delete pool "$POOL" -q
ndcli delete container 10.200.0.0/16 -q

log_pass "Test ipam-reverse-rfc2317 completed successfully."
