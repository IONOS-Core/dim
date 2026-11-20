#!/bin/bash
#
# suites/test-ipam-delegation-boundary-conflict.sh
# E2E Test: Delegating boundary /25 subnets must succeed and not be blocked by internal reservations
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="boundary-bug-pool"
CONTAINER="192.168.0.0/16"
SUBNET="192.168.230.0/24"
DELEG_UPPER="192.168.230.128/25"

cleanup_boundary() {
    ndcli modify pool "$POOL" remove delegation "$DELEG_UPPER" -q 2>/dev/null || true
    ndcli modify pool "$POOL" remove subnet "$SUBNET" -q 2>/dev/null || true
    ndcli delete pool "$POOL" -q 2>/dev/null || true
    delete_zone_all_views "230.168.192.in-addr.arpa"
    ndcli delete container "$CONTAINER" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_boundary

step "Create IPAM Container, Pool, and /24 Subnet ($SUBNET)"
ndcli create container "$CONTAINER" -q
ndcli create pool "$POOL" -q
ndcli modify pool "$POOL" add subnet "$SUBNET" -q

step "Trigger: Attempt to delegate upper half (/25) containing broadcast (.255) address"
set +e
OUTPUT=$(ndcli modify pool "$POOL" mark delegation "$DELEG_UPPER" 2>&1)
EXIT_CODE=$?
set -e

step "Assert: Delegation command succeeded without 'has children' rejection"
assert_exit_code "Mark delegation $DELEG_UPPER" 0 "$EXIT_CODE" "$OUTPUT"

STATUS=$(ndcli show delegation "$DELEG_UPPER" | grep '^status:' | cut -d':' -f2)
assert_equals "Delegation status in IPAM" "Delegation" "$STATUS"

step "Cleanup"
cleanup_boundary

log_pass "Boundary delegation test passed."
