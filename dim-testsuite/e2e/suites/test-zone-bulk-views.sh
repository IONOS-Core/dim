#!/bin/bash
#
# suites/test-zone-bulk-views.sh
# End-to-end test verifying multi-view subzone creation and bulk record migration integrity.
# Validates that when converting a subdomain into a dedicated subzone:
#   - Every view in the parent zone gets a corresponding view in the child zone.
#   - All existing resource records across all views are transferred without data loss.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

PARENT_ZONE="bulk-parent.com"
SUB_ZONE="corp.${PARENT_ZONE}"

step "Clean up previous runs"
delete_zone_all_views "$SUB_ZONE"
delete_zone_all_views "$PARENT_ZONE"

step "Create parent zone with multiple views (default, internal, external)"
ndcli create zone "$PARENT_ZONE"
ndcli modify zone "$PARENT_ZONE" create view internal
ndcli modify zone "$PARENT_ZONE" create view external

step "Populate bulk records under ${SUB_ZONE} across views"
# Add 10 records per view (total 40 records)
for i in $(seq 1 10); do
    ndcli create rr "srv${i}.${SUB_ZONE}." A "10.100.1.${i}" view internal -q
    ndcli create rr "srv${i}.${SUB_ZONE}." A "1.200.1.${i}" view external -q
    ndcli create rr "srv${i}.${SUB_ZONE}." A "192.168.1.${i}" view default -q
    ndcli create rr "srv${i}.${SUB_ZONE}." TXT "bulk test record ${i}" view default -q
done

COUNT_PRE=$(ndcli list rrs "*.${SUB_ZONE}." -H | wc -l)
echo "  Total records created before subzone conversion: $COUNT_PRE"

if [ "$COUNT_PRE" -lt 40 ]; then
    log_fail "Failed to populate expected record volume (got $COUNT_PRE, expected >= 40)."
fi
log_pass "Bulk records successfully created across views."

step "Create subzone ${SUB_ZONE} to trigger automatic view creation and record migration"
OUTPUT=$(ndcli create zone "$SUB_ZONE" 2>&1)
echo "$OUTPUT"

step "Verify child zone views were created matching parent views"
CHILD_VIEWS=$(ndcli list zone "$SUB_ZONE" views -H | sort | tr '\n' ' ' | xargs)
echo "  Created child views: $CHILD_VIEWS"

if [[ "$CHILD_VIEWS" != *"default"* ]] || [[ "$CHILD_VIEWS" != *"internal"* ]] || [[ "$CHILD_VIEWS" != *"external"* ]]; then
    log_fail "Child zone did not inherit all parent views! (Got: $CHILD_VIEWS)"
fi
log_pass "Child zone inherited all views: $CHILD_VIEWS"

step "Verify all bulk records migrated cleanly into subzone views without deletions"
if echo "$OUTPUT" | grep -qi "deleted RR"; then
    log_fail "Records were deleted during subzone conversion:\n$(echo "$OUTPUT" | grep -i "deleted RR")"
fi

COUNT_POST_CHILD=$(ndcli list rrs "*.${SUB_ZONE}." -H | wc -l)
echo "  Total records in subzone after migration: $COUNT_POST_CHILD"

if [ "$COUNT_POST_CHILD" -lt "$COUNT_PRE" ]; then
    log_fail "Record count decreased after migration! (Before: $COUNT_PRE, After: $COUNT_POST_CHILD)"
fi
log_pass "All $COUNT_POST_CHILD records preserved and migrated into subzone."

step "Cleanup"
delete_zone_all_views "$SUB_ZONE"
delete_zone_all_views "$PARENT_ZONE"

log_pass "Test subzone-bulk-views completed successfully."
