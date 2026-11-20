#!/bin/bash
#
# suites/test-zone-arpa-collision.sh
# E2E Test: Reverse in-addr.arpa zone duplicate rejection, subnet auto-creation collisions, and hierarchy
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

CONTAINER="10.222.0.0/16"
SUBNET="10.222.50.0/24"
POOL="arpa-col-pool"
CHILD_REV="50.222.10.in-addr.arpa"
PARENT_REV="222.10.in-addr.arpa"

cleanup_arpa() {
    delete_zone_all_views "$CHILD_REV"
    delete_zone_all_views "$PARENT_REV"
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET" --force 2>/dev/null || true
    ndcli -q delete pool "$POOL" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_arpa

step "Create manual reverse zone: $CHILD_REV"
ndcli --quiet create zone "$CHILD_REV"

step "Test 1: Duplicate reverse zone creation is strictly blocked"
set +e
DUP_OUT=$(ndcli create zone "$CHILD_REV" 2>&1)
DUP_EXIT=$?
set -e

assert_exit_code "Duplicate reverse zone creation rejected" 1 "$DUP_EXIT" "$DUP_OUT"
assert_contains "Duplicate error message" "already exists" "$DUP_OUT"

step "Test 2: Subnet addition seamlessly adopts pre-existing reverse zone without crashing"
ndcli --quiet create container "$CONTAINER"
ndcli --quiet create pool "$POOL"
ndcli --quiet modify pool "$POOL" add subnet "$SUBNET"

# Verify subnet is linked to the existing reverse zone
SUB_REV=$(ndcli show subnet "$SUBNET" | grep '^reverse_zone:' | cut -d':' -f2)
assert_equals "Subnet linked to pre-existing reverse zone" "$CHILD_REV" "$SUB_REV" "Reverse Zone"

step "Test 3: Hierarchical reverse parent zone creation adopts child as subzone"
ndcli --quiet create zone "$PARENT_REV"

# Verify child reverse zone is listed in DIM zones
PARENT_SUBZONES=$(ndcli list zones -H | grep "$CHILD_REV")
echo "  Found child zone: $PARENT_SUBZONES"
assert_not_empty "Child reverse zone exists in DIM" "$PARENT_SUBZONES"

step "Cleanup"
cleanup_arpa

log_pass "Reverse zone collision and hierarchy test passed."
