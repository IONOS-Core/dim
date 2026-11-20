#!/bin/bash
#
# suites/test-ipam-container-move-vrf.sh
# E2E Test: IPAM container transfer across Layer3domains (VRFs), collision protection, and IP re-scoping
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

L3_ALPHA="vrf-alpha"
L3_BETA="vrf-beta"
ROOT_CONTAINER="10.150.0.0/16"
SUB_CONTAINER="10.150.4.0/22"
HOST_IP="10.150.4.1"
ZONE="vrf-move-test.com"

cleanup_vmove() {
    delete_zone_all_views "$ZONE"
    ndcli -q delete container "$SUB_CONTAINER" layer3domain "$L3_BETA" 2>/dev/null || true
    ndcli -q delete container "$SUB_CONTAINER" layer3domain "$L3_ALPHA" 2>/dev/null || true
    ndcli -q delete container "$ROOT_CONTAINER" layer3domain "$L3_BETA" 2>/dev/null || true
    ndcli -q delete container "$ROOT_CONTAINER" layer3domain "$L3_ALPHA" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_ALPHA" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_BETA" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_vmove

step "Setup source and target Layer3domains with root containers"
ndcli --quiet create layer3domain "$L3_ALPHA" type vrf rd 0:201
ndcli --quiet create layer3domain "$L3_BETA" type vrf rd 0:202

ndcli --quiet create container "$ROOT_CONTAINER" layer3domain "$L3_ALPHA"
ndcli --quiet create container "$ROOT_CONTAINER" layer3domain "$L3_BETA"
ndcli --quiet create container "$SUB_CONTAINER" layer3domain "$L3_ALPHA"

ndcli --quiet create zone "$ZONE"
ndcli --quiet create rr "h1.${ZONE}." A "$HOST_IP" layer3domain "$L3_ALPHA"

IP_SCOPE_INIT=$(ndcli show ip "$HOST_IP" layer3domain "$L3_ALPHA" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Initial IP scope is vrf-alpha" "$L3_ALPHA" "$IP_SCOPE_INIT" "Layer3domain"

step "Test 1: Conflict Guard - Moving container is blocked if target VRF already has collision"
ndcli --quiet create container "$SUB_CONTAINER" layer3domain "$L3_BETA"

set +e
COLLISION_OUT=$(ndcli modify container "$SUB_CONTAINER" layer3domain "$L3_ALPHA" move to "$L3_BETA" 2>&1)
EXIT_COLLISION=$?
set -e

assert_exit_code "Container move collision rejected" 1 "$EXIT_COLLISION" "$COLLISION_OUT"
assert_contains "Collision error message" "already exists in layer3domain" "$COLLISION_OUT"

# Delete collision container so move can proceed
ndcli --quiet delete container "$SUB_CONTAINER" layer3domain "$L3_BETA"

step "Test 2: Move container to target VRF (vrf-beta)"
ndcli modify container "$SUB_CONTAINER" layer3domain "$L3_ALPHA" move to "$L3_BETA"

# Verify container now exists in target VRF
CONTAINER_IN_BETA=$(ndcli show container "$SUB_CONTAINER" layer3domain "$L3_BETA" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Container moved to target VRF" "$L3_BETA" "$CONTAINER_IN_BETA" "Layer3domain"

# Verify static IP inside container was re-scoped to vrf-beta
IP_SCOPE_AFTER=$(ndcli show ip "$HOST_IP" layer3domain "$L3_BETA" | grep '^layer3domain:' | cut -d':' -f2)
assert_equals "Static IP re-scoped to target VRF" "$L3_BETA" "$IP_SCOPE_AFTER" "Layer3domain"

step "Cleanup"
cleanup_vmove

log_pass "Cross-VRF container transfer and IP re-scoping test passed."
