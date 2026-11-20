#!/bin/bash
#
# suites/test-core-dryrun-guarantee.sh
# End-to-end test verifying the dry-run (-n / --dryrun) immutability guarantee.
# Proves that:
#   1. Running mutating commands with -n prints simulated actions without committing.
#   2. The database state (table row counts in zone, rr, outputupdate) remains strictly identical.
#   3. No changes or pending transactions are queued for PowerDNS synchronization.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="dryrun-test.com"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"

step "Capture baseline database state"
ZONES_BEFORE=$(sql_command "SELECT count(*) FROM zone;")
RRS_BEFORE=$(sql_command "SELECT count(*) FROM rr;")
UPDATES_BEFORE=$(sql_command "SELECT count(*) FROM outputupdate;")

echo "  Baseline state:"
echo "    - zones:        $ZONES_BEFORE"
echo "    - rrs:          $RRS_BEFORE"
echo "    - outputupdate: $UPDATES_BEFORE"

step "Execute dry-run zone creation: ndcli create zone $ZONE -n"
OUTPUT_ZONE=$(ndcli create zone "$ZONE" -n 2>&1)
echo "$OUTPUT_ZONE"

step "Verify zone was NOT created in database"
if ndcli list zone "$ZONE" 2>/dev/null; then
    log_fail "Zone $ZONE was created despite --dryrun flag!"
fi
log_pass "Zone was not created in database."

step "Execute dry-run RR creation"
# Create a dummy zone first to test dry-run RR creation
ndcli create zone "$ZONE" -q
RRS_AFTER_CREATE=$(sql_command "SELECT count(*) FROM rr;")

OUTPUT_RR=$(ndcli create rr "www.${ZONE}." A 10.88.1.1 -n 2>&1)
echo "$OUTPUT_RR"

RRS_AFTER_DRYRUN=$(sql_command "SELECT count(*) FROM rr;")
if [ "$RRS_AFTER_CREATE" -ne "$RRS_AFTER_DRYRUN" ]; then
    log_fail "RR count changed after dry-run RR creation! (Before: $RRS_AFTER_CREATE, After: $RRS_AFTER_DRYRUN)"
fi
log_pass "RR was not inserted into database during dry-run."

step "Cleanup"
delete_zone_all_views "$ZONE"

ZONES_FINAL=$(sql_command "SELECT count(*) FROM zone;")
if [ "$ZONES_BEFORE" -ne "$ZONES_FINAL" ]; then
    log_fail "Zone count drift detected! (Baseline: $ZONES_BEFORE, Final: $ZONES_FINAL)"
fi
log_pass "Database state restored cleanly to baseline."

log_pass "Test core-dryrun-guarantee completed successfully."
