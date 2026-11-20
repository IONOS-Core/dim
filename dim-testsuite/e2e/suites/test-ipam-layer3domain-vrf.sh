#!/bin/bash
#
# suites/test-ipam-layer3domain-vrf.sh
# End-to-end test verifying multi-tenant Layer3domain (VRF) overlap isolation.
# Proves that:
#   1. Overlapping subnets (RFC 1918) can safely coexist across different Layer3domains.
#   2. Colliding subnets within the SAME Layer3domain are strictly rejected.
#   3. Identical IP addresses can be allocated concurrently in separate VRFs without cross-talk.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

L3_BLUE="vrf-blue"
L3_GREEN="vrf-green"
POOL_BLUE="pool-blue"
POOL_GREEN="pool-green"
OVERLAP_NET="10.50.1.0/24"
OVERLAP_CONTAINER="10.50.0.0/16"
TARGET_IP="10.50.1.42"

cleanup_vrf() {
    delete_zone_all_views "vrf-test.com"
    delete_zone_all_views "1.50.10.in-addr.arpa"
    ndcli modify pool "$POOL_BLUE" remove subnet "$OVERLAP_NET" -q 2>/dev/null || true
    ndcli modify pool "$POOL_GREEN" remove subnet "$OVERLAP_NET" -q 2>/dev/null || true
    ndcli delete pool "$POOL_BLUE" -q 2>/dev/null || true
    ndcli delete pool "$POOL_GREEN" -q 2>/dev/null || true
    ndcli delete container "$OVERLAP_CONTAINER" layer3domain "$L3_BLUE" -q 2>/dev/null || true
    ndcli delete container "$OVERLAP_CONTAINER" layer3domain "$L3_GREEN" -q 2>/dev/null || true
    ndcli delete layer3domain "$L3_BLUE" -q 2>/dev/null || true
    ndcli delete layer3domain "$L3_GREEN" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_vrf

step "Create isolated Layer3domains (VRFs)"
ndcli create layer3domain "$L3_BLUE" type vrf rd 0:101
ndcli create layer3domain "$L3_GREEN" type vrf rd 0:102
log_pass "Layer3domains created."

step "Create identical overlapping IPAM structures in both VRFs"
# Blue VRF
ndcli create container "$OVERLAP_CONTAINER" layer3domain "$L3_BLUE" -q
ndcli create pool "$POOL_BLUE" layer3domain "$L3_BLUE" -q
ndcli modify pool "$POOL_BLUE" add subnet "$OVERLAP_NET" -q

# Green VRF - attempt without --allow-overlap must fail (DIM safety gate)
ndcli create container "$OVERLAP_CONTAINER" layer3domain "$L3_GREEN" -q
ndcli create pool "$POOL_GREEN" layer3domain "$L3_GREEN" -q

if ndcli modify pool "$POOL_GREEN" add subnet "$OVERLAP_NET" 2>/dev/null; then
    log_fail "Expected cross-VRF overlap without --allow-overlap to fail, but it succeeded!"
else
    log_pass "Cross-VRF overlap safely rejected without --allow-overlap flag."
fi

# Green VRF - with explicit --allow-overlap
ndcli modify pool "$POOL_GREEN" add subnet "$OVERLAP_NET" --allow-overlap -q
log_pass "Identical subnets ($OVERLAP_NET) authorized with --allow-overlap."

step "Test Intra-VRF collision rejection"
# Attempting to add an overlapping subnet within the SAME VRF must fail
if ndcli modify pool "$POOL_BLUE" add subnet "10.50.1.128/25" 2>/dev/null; then
    log_fail "Expected subnet overlap collision within same VRF to fail, but it succeeded!"
else
    log_pass "Intra-VRF subnet collision correctly rejected."
fi

step "Allocate identical static IPs concurrently across VRFs"
ZONE_VRF="vrf-test.com"
ndcli create zone "$ZONE_VRF" -q

ndcli create rr "blue.${ZONE_VRF}." A "$TARGET_IP" layer3domain "$L3_BLUE" --allow-overlap -q
ndcli create rr "green.${ZONE_VRF}." A "$TARGET_IP" layer3domain "$L3_GREEN" --allow-overlap -q

BLUE_IP=$(ndcli show ip "$TARGET_IP" layer3domain "$L3_BLUE" | grep '^layer3domain:' | cut -d':' -f2)
GREEN_IP=$(ndcli show ip "$TARGET_IP" layer3domain "$L3_GREEN" | grep '^layer3domain:' | cut -d':' -f2)

echo "  Blue IP scope:  $BLUE_IP"
echo "  Green IP scope: $GREEN_IP"

if [ "$BLUE_IP" != "$L3_BLUE" ] || [ "$GREEN_IP" != "$L3_GREEN" ]; then
    log_fail "VRF IP scoping failure! Expected $L3_BLUE / $L3_GREEN, got '$BLUE_IP' / '$GREEN_IP'"
fi
log_pass "Identical IPs coexist in separate Layer3domains without cross-contamination."

step "Cleanup"
cleanup_vrf

log_pass "Test ipam-layer3domain-vrf completed successfully."
