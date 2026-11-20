#!/bin/bash
#
# suites/test-dnssec-multiview-ds.sh
# E2E Test: Multi-view parent-child DNSSEC DS delegation broadcast and synchronized purge
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

PARENT_ZONE="mv-ds-parent.com"
CHILD_SUB="sub.${PARENT_ZONE}"

cleanup_mv_ds() {
    delete_zone_all_views "$CHILD_SUB"
    delete_zone_all_views "$PARENT_ZONE"
}

step "Clean up previous runs"
cleanup_mv_ds

step "Create multi-view parent zone (default, internal, external) and enable DNSSEC"
ndcli --quiet create zone "$PARENT_ZONE"
ndcli --quiet modify zone "$PARENT_ZONE" create view internal
ndcli --quiet modify zone "$PARENT_ZONE" create view external
ndcli --quiet modify zone "$PARENT_ZONE" dnssec enable 8 ksk 2048 zsk 1024

step "Create child subzone and enable DNSSEC"
ndcli --quiet create zone "$CHILD_SUB"
ndcli --quiet modify zone "$CHILD_SUB" dnssec enable 8 ksk 2048 zsk 1024

step "Test 1: Verify DS record is automatically broadcast into ALL views of the parent zone"
# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
DS_DEFAULT=$(ndcli list zone "$PARENT_ZONE" view default -H | grep "sub.*DS" | wc -l)
# shellcheck disable=SC2126
DS_INTERNAL=$(ndcli list zone "$PARENT_ZONE" view internal -H | grep "sub.*DS" | wc -l)
# shellcheck disable=SC2126
DS_EXTERNAL=$(ndcli list zone "$PARENT_ZONE" view external -H | grep "sub.*DS" | wc -l)

echo "  DS records in parent views: default=$DS_DEFAULT, internal=$DS_INTERNAL, external=$DS_EXTERNAL"

assert_equals "DS record created in parent default view" "1" "$DS_DEFAULT" "Count"
assert_equals "DS record created in parent internal view" "1" "$DS_INTERNAL" "Count"
assert_equals "DS record created in parent external view" "1" "$DS_EXTERNAL" "Count"

step "Test 2: Disable DNSSEC on child subzone and verify synchronized purge across all parent views"
ndcli --quiet modify zone "$CHILD_SUB" dnssec disable

# shellcheck disable=SC2126
DS_DEFAULT_AFTER=$(ndcli list zone "$PARENT_ZONE" view default -H | grep "sub.*DS" | wc -l)
# shellcheck disable=SC2126
DS_INTERNAL_AFTER=$(ndcli list zone "$PARENT_ZONE" view internal -H | grep "sub.*DS" | wc -l)
# shellcheck disable=SC2126
DS_EXTERNAL_AFTER=$(ndcli list zone "$PARENT_ZONE" view external -H | grep "sub.*DS" | wc -l)

echo "  DS records after disable: default=$DS_DEFAULT_AFTER, internal=$DS_INTERNAL_AFTER, external=$DS_EXTERNAL_AFTER"

assert_equals "DS record purged from parent default view" "0" "$DS_DEFAULT_AFTER" "Count"
assert_equals "DS record purged from parent internal view" "0" "$DS_INTERNAL_AFTER" "Count"
assert_equals "DS record purged from parent external view" "0" "$DS_EXTERNAL_AFTER" "Count"

step "Cleanup"
cleanup_mv_ds

log_pass "Multi-view DS delegation broadcast and synchronized purge test passed."
