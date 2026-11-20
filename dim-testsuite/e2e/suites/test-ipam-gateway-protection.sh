#!/bin/bash
#
# suites/test-ipam-gateway-protection.sh
# E2E Test: Subnet Default Gateway protection against dynamic pool allocation
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="gw-protect-pool"
CONTAINER="192.168.0.0/16"
SUBNET="192.168.195.0/29"
GW_IP="192.168.195.1"
EXPECTED_HOST_IP="192.168.195.2"
ZONE="gw-protect.com"
REV_ZONE="195.168.192.in-addr.arpa"

cleanup_gw() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET" 2>/dev/null || true
    ndcli -q delete pool "$POOL" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_gw

step "Create Container, Pool, and Subnet with configured Gateway ($GW_IP)"
ndcli -q create container "$CONTAINER"
ndcli -q create pool "$POOL"
ndcli -q modify pool "$POOL" add subnet "$SUBNET" gw "$GW_IP"
ndcli -q create zone "$ZONE"

GW_CONFIGURED=$(ndcli show subnet "$SUBNET" | grep '^gateway:' | cut -d':' -f2)
assert_equals "Subnet gateway configured" "$GW_IP" "$GW_CONFIGURED" "Gateway IP"

step "Test 1: Request first dynamic IP from pool (must skip gateway and allocate $EXPECTED_HOST_IP)"
ndcli -q create rr "host1.${ZONE}." from "$POOL"

ALLOC_IP=$(ndcli list rrs "host1.${ZONE}." A -H | awk '{print $5}')
echo "  Allocated host IP from pool: $ALLOC_IP"

# In a properly protected pool, the allocator must skip .1 (gateway) and allocate .2
assert_equals "Allocated host IP must not be the Gateway IP" "$EXPECTED_HOST_IP" "$ALLOC_IP" "IP"

step "Cleanup"
cleanup_gw

log_pass "Gateway protection test passed."
