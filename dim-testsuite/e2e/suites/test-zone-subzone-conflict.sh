#!/bin/bash
#
# suites/test-zone-subzone-conflict.sh
# E2E Test: Subzone creation must not delete parent records on apex collision
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

PARENT_ZONE="conflict-parent.com"
SUB_ZONE="sub.${PARENT_ZONE}"

step "Clean up previous runs"
delete_zone_all_views "$SUB_ZONE"
delete_zone_all_views "$PARENT_ZONE"

step "Create parent zone with views and apex records"
ndcli create zone "$PARENT_ZONE" -q
ndcli modify zone "$PARENT_ZONE" create view internal -q
ndcli modify zone "$PARENT_ZONE" create view external -q
ndcli create rr "node1.${SUB_ZONE}." A 10.0.0.1 view internal -q
ndcli create rr "node1.${SUB_ZONE}." A 1.2.3.4 view external -q
ndcli create rr "${SUB_ZONE}." CNAME target.external.com. view external -q

step "Trigger: Create subzone $SUB_ZONE"
OUTPUT=$(ndcli create zone "$SUB_ZONE" 2>&1 || true)
echo "$OUTPUT"

step "Assert: No records were deleted during subzone creation"
DELETED_COUNT=$(echo "$OUTPUT" | grep -c -i "deleted RR" || true)
assert_equals "Deleted records during subzone creation" "0" "$DELETED_COUNT" "Count"

step "Cleanup"
delete_zone_all_views "$SUB_ZONE"
delete_zone_all_views "$PARENT_ZONE"

log_pass "Subzone conflict test passed."
