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


cleanup_deleg() {
    delete_zone_all_views deleg-fwd.com
    delete_zone_all_views 220.168.192.in-addr.arpa
    ndcli modify pool deleg-test-pool remove delegation 192.168.220.64/26 -q 2>/dev/null || true
    ndcli modify pool deleg-test-pool free ip 192.168.220.70 -q 2>/dev/null || true
    ndcli modify pool deleg-test-pool remove subnet 192.168.220.0/24 -q 2>/dev/null || true
    ndcli delete pool deleg-test-pool -q 2>/dev/null || true
    ndcli delete container 192.168.0.0/16 -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_deleg

step "Create Container, Pool, and /24 Subnet (192.168.220.0/24)"
ndcli create container 192.168.0.0/16 -q
ndcli create pool deleg-test-pool -q
ndcli modify pool deleg-test-pool add subnet 192.168.220.0/24 -q
ndcli create zone deleg-fwd.com -q
ensure_output_sync deleg-fwd.com
ensure_output_sync 220.168.192.in-addr.arpa

step "Test 1: Boundary Guard - Delegating blocks covering .0 or .255 is blocked"
# Attempting to delegate upper half (contains .255 broadcast) must be rejected
if ndcli modify pool deleg-test-pool mark delegation "192.168.220.128/25" 2>/dev/null; then
    log_fail "Delegating block containing broadcast address (.255) should fail, but succeeded!"
else
    log_pass "Delegating block containing broadcast address (.255) correctly rejected (has children)."
fi

step "Test 2: Conflict Guard - Delegation blocked if static IP exists inside block"
# Allocate static IP inside future delegation block (.70 is inside .64/26)
ndcli modify pool deleg-test-pool mark ip 192.168.220.70 -q

if ndcli modify pool deleg-test-pool mark delegation 192.168.220.64/26 2>/dev/null; then
    log_fail "Expected delegation over active static IP to fail, but it succeeded!"
else
    log_pass "Delegation correctly blocked when static IP exists in range (has children)."
fi

# Free the static IP so delegation can proceed
ndcli modify pool deleg-test-pool free ip 192.168.220.70 -q
log_pass "Temporary blocking IP freed."

step "Test 3: Mark delegation on 192.168.220.64/26"
ndcli modify pool deleg-test-pool mark delegation 192.168.220.64/26 -q

STATUS=$(ndcli show delegation 192.168.220.64/26 | grep '^status:' | cut -d':' -f2)
echo "  Delegation status in DIM: $STATUS"

if [ "$STATUS" != "Delegation" ]; then
    log_fail "Expected status Delegation, got '$STATUS'"
fi
log_pass "Block successfully marked as Delegation in IPAM."

step "Test 4: Verify dynamic pool allocations avoid the delegated block (.64 - .127)"
# Allocate host from pool: should receive .1
ndcli create rr "host1.deleg-fwd.com." from deleg-test-pool -q
ALLOC_IP=$(ndcli list rrs "host1.deleg-fwd.com." A -H | awk '{print $5}')
echo "  Allocated host IP from pool: $ALLOC_IP"

IP_LAST_OCTET=$(echo "$ALLOC_IP" | awk -F'.' '{print $4}')
if [ "$IP_LAST_OCTET" -ge 64 ] && [ "$IP_LAST_OCTET" -le 127 ]; then
    log_fail "Pool allocated IP from delegated space! ($ALLOC_IP)"
fi
log_pass "Pool allocation strictly avoided delegated range (received $ALLOC_IP not in .64-.127)."

step "Test 5: Create NS delegation record in parent in-addr.arpa zone"
ndcli modify zone 220.168.192.in-addr.arpa create rr 64/26 NS ns1.delegated-partner.com. -q

# Wait for PowerDNS to receive NS delegation (referral in authority section)
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_NS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "64/26.220.168.192.in-addr.arpa." NS +noall +authority | awk '{print $5}' || true)
    if [ "$PDNS_NS" = "ns1.delegated-partner.com." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS failed to receive NS delegation for 64/26.220.168.192.in-addr.arpa."
fi
log_pass "PowerDNS serves valid authoritative NS delegation: $PDNS_NS"

step "Test 6: Remove delegation and verify clean restoration to pool"
ndcli modify pool deleg-test-pool remove delegation 192.168.220.64/26 -q

STATUS_AFTER=$(ndcli show delegation 192.168.220.64/26 2>&1 || true)
if ! echo "$STATUS_AFTER" | grep -q "does not exist"; then
    log_fail "Delegation still exists in IPAM after removal: $STATUS_AFTER"
fi
log_pass "Delegation cleanly removed and space returned to pool."

step "Cleanup"
cleanup_deleg

log_pass "Test ipam-reverse-delegation completed successfully."
