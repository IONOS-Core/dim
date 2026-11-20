#!/bin/bash
#
# suites/test-ipam-subnet-move.sh
# E2E Test: Live subnet transfer between IP pools (--allow-move) and active IP re-scoping
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

CONTAINER="192.168.0.0/16"
SUBNET="192.168.175.0/24"
POOL_SRC="pool-source"
POOL_DST="pool-target"
ZONE="move-eval.com"
REV_ZONE="175.168.192.in-addr.arpa"
HOST_IP="192.168.175.1"

cleanup_move() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli -q modify pool "$POOL_DST" remove subnet "$SUBNET" --force 2>/dev/null || true
    ndcli -q modify pool "$POOL_SRC" remove subnet "$SUBNET" --force 2>/dev/null || true
    ndcli -q delete pool "$POOL_SRC" 2>/dev/null || true
    ndcli -q delete pool "$POOL_DST" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_move

step "Setup container, source and destination pools, and /24 subnet in source pool"
ndcli -q create container "$CONTAINER"
ndcli -q create pool "$POOL_SRC"
ndcli -q create pool "$POOL_DST"
ndcli -q modify pool "$POOL_SRC" add subnet "$SUBNET"
ndcli -q create zone "$ZONE"
ensure_output_sync "$ZONE"
ensure_output_sync "$REV_ZONE"

step "Allocate active host from source pool"
ndcli -q create rr "srv1.${ZONE}." from "$POOL_SRC"

ALLOCATED_IP=$(ndcli list rrs "srv1.${ZONE}." A -H | awk '{print $5}')
assert_equals "Allocated initial host IP" "$HOST_IP" "$ALLOCATED_IP" "IP"

INITIAL_POOL_ATTR=$(ndcli show ip "$HOST_IP" | grep '^pool:' | cut -d':' -f2)
assert_equals "Initial IP pool attribute" "$POOL_SRC" "$INITIAL_POOL_ATTR" "Pool Name"

step "Test 1: Guard - Moving subnet to destination pool without --allow-move is blocked"
set +e
BLOCKED_MOVE=$(ndcli modify pool "$POOL_DST" add subnet "$SUBNET" 2>&1)
EXIT_BLOCKED=$?
set -e

assert_exit_code "Move without --allow-move rejected" 1 "$EXIT_BLOCKED" "$BLOCKED_MOVE"
assert_contains "Move rejection error message" "because it is part of the pool" "$BLOCKED_MOVE"

step "Test 2: Move subnet to destination pool using --allow-move"
ndcli -q modify pool "$POOL_DST" add subnet "$SUBNET" --allow-move

SUBNET_POOL_AFTER=$(ndcli show subnet "$SUBNET" | grep '^pool:' | cut -d':' -f2)
assert_equals "Subnet pool membership updated to target" "$POOL_DST" "$SUBNET_POOL_AFTER" "Pool Name"

IP_POOL_AFTER=$(ndcli show ip "$HOST_IP" | grep '^pool:' | cut -d':' -f2)
assert_equals "Active IP pool membership re-scoped to target pool" "$POOL_DST" "$IP_POOL_AFTER" "Pool Name"

step "Test 3: Destination pool successfully allocates next IP from moved subnet"
ndcli -q create rr "srv2.${ZONE}." from "$POOL_DST"

NEXT_IP=$(ndcli list rrs "srv2.${ZONE}." A -H | awk '{print $5}')
assert_equals "Next allocation from target pool" "192.168.175.2" "$NEXT_IP" "IP"

step "Test 4: Verify uninterrupted DNS resolution in PowerDNS"
PDNS_SRV1=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv1.${ZONE}." A +short)
assert_equals "PowerDNS serves srv1 after subnet move" "$HOST_IP" "$PDNS_SRV1" "IP"

step "Cleanup"
cleanup_move

log_pass "Subnet pool transfer test passed."
