#!/bin/bash
#
# suites/test-ipam-pool-vrf-move.sh
# E2E Test: Full IP pool migration across Layer3domains (VRFs), collision blocking, and reverse view migration
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

L3_SRC="vrf-mv-src"
L3_DST="vrf-mv-dst"
ROOT_CONTAINER="10.0.0.0/8"
SUBNET="10.160.5.0/24"
HOST_IP="10.160.5.10"
POOL="pool-vrf-move"
ZONE="vrf-pool-eval.com"
REV_ZONE="5.160.10.in-addr.arpa"

cleanup_pvrf() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET" --force 2>/dev/null || true
    ndcli -q delete pool "$POOL" 2>/dev/null || true
    ndcli -q delete container "$SUBNET" layer3domain "$L3_DST" 2>/dev/null || true
    ndcli -q delete container "$ROOT_CONTAINER" layer3domain "$L3_DST" 2>/dev/null || true
    ndcli -q delete container "$ROOT_CONTAINER" layer3domain "$L3_SRC" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_SRC" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_DST" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_pvrf

step "Setup source and destination Layer3domains with root containers"
ndcli --quiet create layer3domain "$L3_SRC" type vrf rd 0:401
ndcli --quiet create layer3domain "$L3_DST" type vrf rd 0:402

ndcli --quiet create container "$ROOT_CONTAINER" layer3domain "$L3_SRC"
ndcli --quiet create container "$ROOT_CONTAINER" layer3domain "$L3_DST"

ndcli --quiet create pool "$POOL" layer3domain "$L3_SRC"
ndcli --quiet modify pool "$POOL" add subnet "$SUBNET"
ndcli --quiet create zone "$ZONE"
ndcli --quiet create rr "srv1.${ZONE}." A "$HOST_IP" layer3domain "$L3_SRC"

POOL_L3_INIT=$(ndcli show pool "$POOL" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Initial pool layer3domain is source VRF" "$L3_SRC" "$POOL_L3_INIT" "Layer3domain"

step "Test 1: Conflict Guard - Pool move rejected if target VRF contains collision"
ndcli --quiet create container "$SUBNET" layer3domain "$L3_DST"

set +e
COLLISION_MOVE=$(ndcli modify pool "$POOL" set layer3domain "$L3_DST" 2>&1)
EXIT_COLLISION=$?
set -e

assert_exit_code "Collision move rejected" 1 "$EXIT_COLLISION" "$COLLISION_MOVE"
assert_contains "Collision error message" "are in the way" "$COLLISION_MOVE"

# Remove blocking collision container
ndcli --quiet delete container "$SUBNET" layer3domain "$L3_DST"

step "Test 2: Move entire pool with subnets and static IPs to target VRF"
ndcli modify pool "$POOL" set layer3domain "$L3_DST"

# Verify pool now belongs to target VRF
POOL_L3_AFTER=$(ndcli show pool "$POOL" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Pool layer3domain updated to target VRF" "$L3_DST" "$POOL_L3_AFTER" "Layer3domain"

# Verify subnet IP and static host were re-scoped to target VRF
IP_L3_AFTER=$(ndcli show ip "$HOST_IP" layer3domain "$L3_DST" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Static host IP re-scoped to target VRF" "$L3_DST" "$IP_L3_AFTER" "Layer3domain"

# Verify reverse PTR record was migrated to the target VRF view in reverse zone
PTR_TARGET_VIEW=$(ndcli list zone "$REV_ZONE" view "$L3_DST" -H | awk '$1 == "10" {print $4}')
assert_equals "Reverse PTR migrated to target VRF view" "srv1.${ZONE}." "$PTR_TARGET_VIEW" "PTR Target"

step "Cleanup"
cleanup_pvrf

log_pass "Cross-VRF pool transfer and reverse view migration test passed."
