#!/bin/bash
#
# suites/test-records-idempotency.sh
# E2E Test: Resource record creation idempotency, round-robin synthesis, and selective deletion
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="idemp-eval-test.com"

cleanup_idemp() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_idemp

step "Create forward zone"
ndcli create zone "$ZONE" --quiet

step "Test 1: Idempotency - Creating identical duplicate record returns exit 0 and already exists message"
ndcli create rr "srv.${ZONE}." A 10.1.1.1 --only-forward --quiet

set +e
DUP_OUT=$(ndcli create rr "srv.${ZONE}." A 10.1.1.1 --only-forward 2>&1)
DUP_EXIT=$?
set -e

assert_exit_code "Duplicate RR creation exit code" 0 "$DUP_EXIT" "$DUP_OUT"
assert_contains "Duplicate RR message" "already exists" "$DUP_OUT"

step "Test 2: Round-Robin - Creating RR on same name with different IP creates second entry"
set +e
RR_OUT=$(ndcli create rr "srv.${ZONE}." A 10.1.1.2 --only-forward 2>&1)
RR_EXIT=$?
set -e

assert_exit_code "Round-robin RR creation exit code" 0 "$RR_EXIT" "$RR_OUT"
assert_contains "Round-robin warning" "creating round robin record" "$RR_OUT"

# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
RR_COUNT=$(ndcli list rrs "srv.${ZONE}." A -H | grep "srv" | wc -l)
assert_equals "Total round-robin entries" "2" "$RR_COUNT" "Count"

step "Test 3: Overwrite Flag - --overwrite-a deletes all previous round-robin entries and sets single new IP"
ndcli create rr "srv.${ZONE}." A 10.1.1.99 --only-forward --overwrite-a --quiet

# shellcheck disable=SC2126
OVERWRITE_COUNT=$(ndcli list rrs "srv.${ZONE}." A -H | grep "srv" | wc -l)
assert_equals "Round-robin entries collapsed to single record" "1" "$OVERWRITE_COUNT" "Count"

ACTIVE_IP=$(ndcli list rrs "srv.${ZONE}." A -H | awk '{print $5}')
assert_equals "Active IP after overwrite" "10.1.1.99" "$ACTIVE_IP" "IP"

step "Test 4: Selective Deletion - Create two entries and delete only one specific value"
ndcli create rr "srv.${ZONE}." A 10.1.1.100 --only-forward --quiet

# Delete only 10.1.1.99
ndcli delete rr "srv.${ZONE}." A 10.1.1.99 --quiet

REMAINING_IP=$(ndcli list rrs "srv.${ZONE}." A -H | awk '{print $5}')
assert_equals "Remaining IP after selective delete" "10.1.1.100" "$REMAINING_IP" "IP"

step "Test 5: Full Type Deletion - Deleting without value deletes all remaining records"
ndcli delete rr "srv.${ZONE}." A --quiet

FINAL_COUNT=$(ndcli list rrs "srv.${ZONE}." A -H 2>/dev/null | grep -c "srv" || true)
assert_equals "Records remaining after full type delete" "0" "$FINAL_COUNT" "Count"

step "Cleanup"
cleanup_idemp

log_pass "Record idempotency and round-robin lifecycle test passed."
