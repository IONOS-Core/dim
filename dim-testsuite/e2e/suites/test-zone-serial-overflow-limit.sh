#!/bin/bash
#
# suites/test-zone-serial-overflow-limit.sh
# E2E Test: Strict enforcement of RFC 1982 32-bit SOA serial upper boundary (4294967295)
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="serial-limit-test.com"
MAX_SERIAL="4294967295"

cleanup_overflow() {
    ndcli modify zone "$ZONE" set serial 2026111001 2>/dev/null || true
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_overflow

step "Create zone and set SOA serial to upper 32-bit limit ($MAX_SERIAL)"
ndcli create zone "$ZONE" --quiet
ndcli modify zone "$ZONE" set serial "$MAX_SERIAL"

CURRENT_SERIAL=$(ndcli list zone "$ZONE" -H | grep "SOA" | awk '{print $7}')
assert_equals "Zone serial initialized to max uint32" "$MAX_SERIAL" "$CURRENT_SERIAL" "Serial"

step "Trigger: Mutate zone by adding RR (forces serial += 1 across 32-bit boundary)"
set +e
OUTPUT=$(ndcli create rr "node1.${ZONE}." A 10.1.1.1 --only-forward 2>&1)
EXIT_CODE=$?
set -e

echo "  Mutation output: $OUTPUT"

step "Assert: DIM strictly enforces RFC 1982 by rejecting serial increment beyond 32 bits"
assert_exit_code "Serial increment beyond 2^32-1 rejected" 1 "$EXIT_CODE" "$OUTPUT"
assert_contains "Rejection message contains invalid serial limit" "Invalid serial: 4294967296" "$OUTPUT"

step "Cleanup"
cleanup_overflow

log_pass "RFC 1982 32-bit serial upper boundary enforcement test passed."
