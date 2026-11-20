#!/bin/bash
#
# suites/test-core-output-view-exclusivity.sh
# E2E Test: Single-view-per-output invariant across zone-groups (output collision prevention)
#
# Invariant:
#   An authoritative output (PowerDNS) can only receive EXACTLY ONE view of any given zone.
#   DIM must strictly prevent routing multiple views of the same zone to the same output,
#   either via direct addition to the same zone-group or indirect addition across zone-groups.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="view-excl-eval.com"
ZG_A="zg-excl-a"
ZG_B="zg-excl-b"

cleanup_excl() {
    delete_zone_all_views "$ZONE"
    ndcli -q modify output out remove zone-group "$ZG_A" 2>/dev/null || true
    ndcli -q modify output out remove zone-group "$ZG_B" 2>/dev/null || true
    ndcli -q delete zone-group "$ZG_A" 2>/dev/null || true
    ndcli -q delete zone-group "$ZG_B" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_excl

step "Setup multi-view zone (default, internal, external) and two zone-groups connected to output out"
ndcli --quiet create zone "$ZONE"
ndcli --quiet modify zone "$ZONE" create view internal
ndcli --quiet modify zone "$ZONE" create view external

ndcli --quiet create zone-group "$ZG_A"
ndcli --quiet create zone-group "$ZG_B"

ndcli --quiet modify output out add zone-group "$ZG_A"
ndcli --quiet modify output out add zone-group "$ZG_B"

step "Add view internal to zone-group $ZG_A (reaches output out)"
ndcli --quiet modify zone-group "$ZG_A" add zone "$ZONE" view internal
log_pass "View internal added to $ZG_A successfully."

step "Test 1: Direct Collision - Adding view external to same zone-group ($ZG_A) is rejected"
set +e
DIRECT_COLLISION_OUT=$(ndcli modify zone-group "$ZG_A" add zone "$ZONE" view external 2>&1)
EXIT_DIRECT=$?
set -e

assert_exit_code "Direct collision addition rejected" 1 "$EXIT_DIRECT" "$DIRECT_COLLISION_OUT"
assert_contains "Rejection cites zone already in group" "already in zone-group" "$DIRECT_COLLISION_OUT"

step "Test 2: Indirect Collision - Adding view external to $ZG_B (attached to same output) is rejected"
set +e
INDIRECT_COLLISION_OUT=$(ndcli modify zone-group "$ZG_B" add zone "$ZONE" view external 2>&1)
EXIT_INDIRECT=$?
set -e

assert_exit_code "Indirect collision addition rejected" 1 "$EXIT_INDIRECT" "$INDIRECT_COLLISION_OUT"
assert_contains "Rejection cites output collision across groups" "because it breaks the output out" "$INDIRECT_COLLISION_OUT"

step "Test 3: Output Connection Collision - Connecting group to output fails if views would collide"
# Detach ZG_B from output -> now adding view external to ZG_B must succeed (no outputs connected)
ndcli --quiet modify output out remove zone-group "$ZG_B"
ndcli --quiet modify zone-group "$ZG_B" add zone "$ZONE" view external
log_pass "View external added to standalone unattached zone-group $ZG_B."

# Now attempt to connect ZG_B back to output out -> must fail because out already has view internal from ZG_A
set +e
ATTACH_COLLISION_OUT=$(ndcli modify output out add zone-group "$ZG_B" 2>&1)
EXIT_ATTACH=$?
set -e

assert_exit_code "Connecting conflicting zone-group to output rejected" 1 "$EXIT_ATTACH" "$ATTACH_COLLISION_OUT"

step "Cleanup"
cleanup_excl

log_pass "Single-view-per-output exclusivity test passed."
