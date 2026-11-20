#!/bin/bash
#
# suites/test-ipam-subnet-adjacent.sh
# E2E Test: Adjacent contiguous subnet coexistence, overlapping CIDR boundary rejection, and dual reverse zones
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

CONTAINER="10.10.0.0/16"
SUBNET_1="10.10.1.0/24"
SUBNET_2="10.10.2.0/24"
OVERLAP_SUPERSET="10.10.0.0/23"
OVERLAP_SUBSET="10.10.1.128/25"
POOL="adjacent-eval-pool"
ZONE="adjacent-eval.com"
REV_1="1.10.10.in-addr.arpa"
REV_2="2.10.10.in-addr.arpa"

cleanup_adjacent() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_1"
    delete_zone_all_views "$REV_2"
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET_1" --force 2>/dev/null || true
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET_2" --force 2>/dev/null || true
    ndcli -q delete pool "$POOL" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_adjacent

step "Create container, pool, and two contiguous adjacent /24 subnets"
ndcli --quiet create container "$CONTAINER"
ndcli --quiet create pool "$POOL"
ndcli --quiet modify pool "$POOL" add subnet "$SUBNET_1"
ndcli --quiet modify pool "$POOL" add subnet "$SUBNET_2"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

# Verify both adjacent subnets exist in pool
POOL_SUBNETS=$(ndcli list pool "$POOL" subnets -H | awk '{print $2}' | sort | tr '\n' ' ' | xargs)
echo "  Pool subnets: $POOL_SUBNETS"
assert_contains "Pool contains Subnet 1" "$SUBNET_1" "$POOL_SUBNETS"
assert_contains "Pool contains Subnet 2" "$SUBNET_2" "$POOL_SUBNETS"

step "Test 1: Conflict Guard - Overlapping superset /23 (10.10.0.0/23) is strictly rejected"
set +e
SUPERSET_OUT=$(ndcli modify pool "$POOL" add subnet "$OVERLAP_SUPERSET" 2>&1)
EXIT_SUPERSET=$?
set -e

assert_exit_code "Overlapping superset /23 rejected" 1 "$EXIT_SUPERSET" "$SUPERSET_OUT"
assert_contains "Superset error message" "cannot be created because it would have non-Delegation blocks as children" "$SUPERSET_OUT"

step "Test 2: Conflict Guard - Overlapping subset /25 (10.10.1.128/25) is strictly rejected"
set +e
SUBSET_OUT=$(ndcli modify pool "$POOL" add subnet "$OVERLAP_SUBSET" 2>&1)
EXIT_SUBSET=$?
set -e

assert_exit_code "Overlapping subset /25 rejected" 1 "$EXIT_SUBSET" "$SUBSET_OUT"
assert_contains "Subset error message" "Block allocations only allowed under Container blocks" "$SUBSET_OUT"

step "Test 3: Allocations from adjacent pool populate both reverse zones"
ndcli --quiet create rr "h1.${ZONE}." A 10.10.1.10
ndcli --quiet create rr "h2.${ZONE}." A 10.10.2.10

PTR1=$(ndcli list rrs "10.${REV_1}." PTR -H | awk '{print $5}')
assert_equals "Reverse PTR in Subnet 1 zone" "h1.${ZONE}." "$PTR1" "PTR Target"

PTR2=$(ndcli list rrs "10.${REV_2}." PTR -H | awk '{print $5}')
assert_equals "Reverse PTR in Subnet 2 zone" "h2.${ZONE}." "$PTR2" "PTR Target"

step "Cleanup"
cleanup_adjacent

log_pass "Adjacent subnet coexistence and overlap prevention test passed."
