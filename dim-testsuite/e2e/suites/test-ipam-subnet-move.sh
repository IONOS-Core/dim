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


cleanup_move() {
    delete_zone_all_views move-eval.com
    delete_zone_all_views 175.168.192.in-addr.arpa
    ndcli -q modify pool pool-target remove subnet 192.168.175.0/24 --force 2>/dev/null || true
    ndcli -q modify pool pool-source remove subnet 192.168.175.0/24 --force 2>/dev/null || true
    ndcli -q delete pool pool-source 2>/dev/null || true
    ndcli -q delete pool pool-target 2>/dev/null || true
    ndcli -q delete container 192.168.0.0/16 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_move

step "Setup container, source and destination pools, and /24 subnet in source pool"
ndcli -q create container 192.168.0.0/16
ndcli -q create pool pool-source
ndcli -q create pool pool-target
ndcli -q modify pool pool-source add subnet 192.168.175.0/24
ndcli -q create zone move-eval.com
ensure_output_sync move-eval.com
ensure_output_sync 175.168.192.in-addr.arpa

step "Allocate active host from source pool"
ndcli -q create rr "srv1.move-eval.com." from pool-source

ALLOCATED_IP=$(ndcli list rrs "srv1.move-eval.com." A -H | awk '{print $5}')
assert_equals "Allocated initial host IP" 192.168.175.1 "$ALLOCATED_IP" "IP"

INITIAL_POOL_ATTR=$(ndcli show ip 192.168.175.1 | grep '^pool:' | cut -d':' -f2)
assert_equals "Initial IP pool attribute" pool-source "$INITIAL_POOL_ATTR" "Pool Name"

step "Test 1: Guard - Moving subnet to destination pool without --allow-move is blocked"
set +e
BLOCKED_MOVE=$(ndcli modify pool pool-target add subnet 192.168.175.0/24 2>&1)
EXIT_BLOCKED=$?
set -e

assert_exit_code "Move without --allow-move rejected" 1 "$EXIT_BLOCKED" "$BLOCKED_MOVE"
assert_contains "Move rejection error message" "because it is part of the pool" "$BLOCKED_MOVE"

step "Test 2: Move subnet to destination pool using --allow-move"
ndcli -q modify pool pool-target add subnet 192.168.175.0/24 --allow-move

SUBNET_POOL_AFTER=$(ndcli show subnet 192.168.175.0/24 | grep '^pool:' | cut -d':' -f2)
assert_equals "Subnet pool membership updated to target" pool-target "$SUBNET_POOL_AFTER" "Pool Name"

IP_POOL_AFTER=$(ndcli show ip 192.168.175.1 | grep '^pool:' | cut -d':' -f2)
assert_equals "Active IP pool membership re-scoped to target pool" pool-target "$IP_POOL_AFTER" "Pool Name"

step "Test 3: Destination pool successfully allocates next IP from moved subnet"
ndcli -q create rr "srv2.move-eval.com." from pool-target

NEXT_IP=$(ndcli list rrs "srv2.move-eval.com." A -H | awk '{print $5}')
assert_equals "Next allocation from target pool" "192.168.175.2" "$NEXT_IP" "IP"

step "Test 4: Verify uninterrupted DNS resolution in PowerDNS"
PDNS_SRV1=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv1.move-eval.com." A +short)
assert_equals "PowerDNS serves srv1 after subnet move" 192.168.175.1 "$PDNS_SRV1" "IP"

step "Cleanup"
cleanup_move

log_pass "Subnet pool transfer test passed."
