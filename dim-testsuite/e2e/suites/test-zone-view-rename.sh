#!/bin/bash
#
# suites/test-zone-view-rename.sh
# E2E Test: Zone view renaming, record retention, and view collision constraints
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="view-rename-test.com"

cleanup_rename() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_rename

step "Create zone and add records in default view"
ndcli create zone "$ZONE" --quiet
ndcli create rr "node1.${ZONE}." A 10.1.1.1 --quiet
ndcli create rr "node2.${ZONE}." A 10.1.1.2 --quiet

VIEWS_INIT=$(ndcli list zone "$ZONE" views -H | xargs)
assert_equals "Initial view is default" "default" "$VIEWS_INIT" "View Name"

step "Test 1: Rename view 'default' to 'internal'"
ndcli modify zone "$ZONE" rename view default to internal

VIEWS_AFTER=$(ndcli list zone "$ZONE" views -H | xargs)
assert_equals "View renamed to internal" "internal" "$VIEWS_AFTER" "View Name"

# Verify records still exist under internal view
RECORDS_INT=$(ndcli list zone "$ZONE" view internal -H)
assert_contains "Record node1 preserved in internal view" "node1" "$RECORDS_INT"
assert_contains "Record node2 preserved in internal view" "node2" "$RECORDS_INT"

# Verify old 'default' view no longer accessible
set +e
DEFAULT_VIEW_OUT=$(ndcli list zone "$ZONE" view default 2>&1)
EXIT_DEFAULT=$?
set -e

assert_exit_code "Querying retired default view fails" 1 "$EXIT_DEFAULT" "$DEFAULT_VIEW_OUT"
assert_contains "Retired view error message" "has no view named default" "$DEFAULT_VIEW_OUT"

step "Test 2: View collision guard - Cannot rename to an already existing view"
ndcli modify zone "$ZONE" create view external --quiet

set +e
COLLISION_OUT=$(ndcli modify zone "$ZONE" rename view internal to external 2>&1)
EXIT_COLLISION=$?
set -e

assert_exit_code "Collision rename rejected" 1 "$EXIT_COLLISION" "$COLLISION_OUT"
assert_contains "Collision error message" "already exists" "$COLLISION_OUT"

step "Test 3: Non-existent view rename guard"
set +e
NONEXIST_OUT=$(ndcli modify zone "$ZONE" rename view ghost to phantom 2>&1)
EXIT_NONEXIST=$?
set -e

assert_exit_code "Renaming non-existent view rejected" 1 "$EXIT_NONEXIST" "$NONEXIST_OUT"
assert_contains "Non-existent view error message" "has no view named ghost" "$NONEXIST_OUT"

step "Cleanup"
cleanup_rename

log_pass "Zone view rename test passed."
