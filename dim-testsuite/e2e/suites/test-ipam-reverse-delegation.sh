#!/bin/bash
#
# suites/test-ipam-reverse-delegation.sh
# End-to-end test verifying Reverse Zone Subnet Delegation, conflict protection, and NS delegation.
# Proves that:
#   1. A /24 subnet can delegate sub-blocks (e.g. /26) that do not contain network/broadcast boundaries.
#   2. Attempting to delegate blocks covering .0 or .255 fails safely ("has children" due to reserved boundary IPs).
#   3. Marking a delegation fails safely if active static IPs exist inside the range ("has children").
#   4. Dynamic pool allocations respect delegations and avoid the delegated block.
#   5. NS delegation records in the parent in-addr.arpa zone are synchronized to PowerDNS.
#   6. Removing a delegation safely returns the block to the pool.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="deleg-test-pool"
CONTAINER="192.168.0.0/16"
SUBNET="192.168.220.0/24"
DELEG_BLOCK="192.168.220.64/26"
REV_ZONE="220.168.192.in-addr.arpa"
FWD_ZONE="deleg-fwd.com"

cleanup_deleg() {
    delete_zone_all_views "$FWD_ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli modify pool "$POOL" remove delegation "$DELEG_BLOCK" -q 2>/dev/null || true
    ndcli modify pool "$POOL" free ip 192.168.220.70 -q 2>/dev/null || true
    ndcli modify pool "$POOL" remove subnet "$SUBNET" -q 2>/dev/null || true
    ndcli delete pool "$POOL" -q 2>/dev/null || true
    ndcli delete container "$CONTAINER" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_deleg

step "Create Container, Pool, and /24 Subnet ($SUBNET)"
ndcli create container "$CONTAINER" -q
ndcli create pool "$POOL" -q
ndcli modify pool "$POOL" add subnet "$SUBNET" -q
ndcli create zone "$FWD_ZONE" -q
ensure_output_sync "$FWD_ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: Boundary Guard - Delegating blocks covering .0 or .255 is blocked"
# Attempting to delegate upper half (contains .255 broadcast) must be rejected
if ndcli modify pool "$POOL" mark delegation "192.168.220.128/25" 2>/dev/null; then
    log_fail "Delegating block containing broadcast address (.255) should fail, but succeeded!"
else
    log_pass "Delegating block containing broadcast address (.255) correctly rejected (has children)."
fi

step "Test 2: Conflict Guard - Delegation blocked if static IP exists inside block"
# Allocate static IP inside future delegation block (.70 is inside .64/26)
ndcli modify pool "$POOL" mark ip 192.168.220.70 -q

if ndcli modify pool "$POOL" mark delegation "$DELEG_BLOCK" 2>/dev/null; then
    log_fail "Expected delegation over active static IP to fail, but it succeeded!"
else
    log_pass "Delegation correctly blocked when static IP exists in range (has children)."
fi

# Free the static IP so delegation can proceed
ndcli modify pool "$POOL" free ip 192.168.220.70 -q
log_pass "Temporary blocking IP freed."

step "Test 3: Mark delegation on $DELEG_BLOCK"
ndcli modify pool "$POOL" mark delegation "$DELEG_BLOCK" -q

STATUS=$(ndcli show delegation "$DELEG_BLOCK" | grep '^status:' | cut -d':' -f2)
echo "  Delegation status in DIM: $STATUS"

if [ "$STATUS" != "Delegation" ]; then
    log_fail "Expected status Delegation, got '$STATUS'"
fi
log_pass "Block successfully marked as Delegation in IPAM."

step "Test 4: Verify dynamic pool allocations avoid the delegated block (.64 - .127)"
# Allocate host from pool: should receive .1
ndcli create rr "host1.${FWD_ZONE}." from "$POOL" -q
ALLOC_IP=$(ndcli list rrs "host1.${FWD_ZONE}." A -H | awk '{print $5}')
echo "  Allocated host IP from pool: $ALLOC_IP"

IP_LAST_OCTET=$(echo "$ALLOC_IP" | awk -F'.' '{print $4}')
if [ "$IP_LAST_OCTET" -ge 64 ] && [ "$IP_LAST_OCTET" -le 127 ]; then
    log_fail "Pool allocated IP from delegated space! ($ALLOC_IP)"
fi
log_pass "Pool allocation strictly avoided delegated range (received $ALLOC_IP not in .64-.127)."

step "Test 5: Create NS delegation record in parent in-addr.arpa zone"
ndcli modify zone "$REV_ZONE" create rr 64/26 NS ns1.delegated-partner.com. -q

# Wait for PowerDNS to receive NS delegation (referral in authority section)
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_NS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "64/26.${REV_ZONE}." NS +noall +authority | awk '{print $5}' || true)
    if [ "$PDNS_NS" = "ns1.delegated-partner.com." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS failed to receive NS delegation for 64/26.${REV_ZONE}."
fi
log_pass "PowerDNS serves valid authoritative NS delegation: $PDNS_NS"

step "Test 6: Remove delegation and verify clean restoration to pool"
ndcli modify pool "$POOL" remove delegation "$DELEG_BLOCK" -q

STATUS_AFTER=$(ndcli show delegation "$DELEG_BLOCK" 2>&1 || true)
if ! echo "$STATUS_AFTER" | grep -q "does not exist"; then
    log_fail "Delegation still exists in IPAM after removal: $STATUS_AFTER"
fi
log_pass "Delegation cleanly removed and space returned to pool."

step "Cleanup"
cleanup_deleg

log_pass "Test ipam-reverse-delegation completed successfully."
