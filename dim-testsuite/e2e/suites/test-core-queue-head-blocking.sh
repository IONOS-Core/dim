#!/bin/bash
#
# suites/test-core-queue-head-blocking.sh
# End-to-end test validating pdns-output queue resilience, Head-of-Line (HoL) blocking behavior,
# and operator recovery.
#
# Threat Model (Catastrophic Outage Scenario):
# If an invalid record or poisoned transaction enters the 'outputupdate' table
# (e.g., custom ALIAS type, corrupted SOA, or orphaned record delete), pdns-output
# retries the head of the queue in an infinite loop.
#
# This test verifies:
#   1. Normal operations process and sync to PowerDNS cleanly.
#   2. A poisoned transaction at the head of the queue halts synchronization for ALL subsequent zones.
#   3. Removing/quarantining the stuck transaction immediately unblocks the daemon and restores sync.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE_CANARY="canary-hol.com"
ZONE_VICTIM="victim-hol.com"

cleanup_hol_test() {
    delete_zone_all_views "$ZONE_VICTIM"
    delete_zone_all_views "$ZONE_CANARY"
    sql_command "DELETE FROM outputupdate WHERE zone_name IN ('${ZONE_CANARY}', '${ZONE_VICTIM}');"
}
trap cleanup_hol_test EXIT INT TERM

step "Clean up previous runs"
cleanup_hol_test

step "Setup Canary and Victim zones"
ndcli --quiet create zone "$ZONE_CANARY"
ensure_output_sync "$ZONE_CANARY"

ndcli --quiet create zone "$ZONE_VICTIM"
ensure_output_sync "$ZONE_VICTIM"

step "Test 1: Normal zone updates sync to PowerDNS cleanly"
ndcli --quiet create rr "init.${ZONE_CANARY}." A 10.10.10.10

SYNCED_CANARY=false
for _ in $(seq 1 20); do
    CANARY_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "init.${ZONE_CANARY}." A +short)
    if [ "$CANARY_ANS" = "10.10.10.10" ]; then
        SYNCED_CANARY=true
        break
    fi
    sleep 1
done
assert_equals "Canary record synchronized to PowerDNS" "true" "$SYNCED_CANARY"

step "Test 2: Inject poisoned transaction at the head of the queue"
# Insert a poisoned transaction into outputupdate that causes pdns-output to throw an exception
POISON_TID="deadbeef00000001"
OUTPUT_ID=$(sql_command "SELECT id FROM output LIMIT 1;")
sql_command "INSERT INTO outputupdate (transaction, action, output_id, zone_name, serial, name, ttl, type, content) \
             VALUES ('${POISON_TID}', 'create_rr', ${OUTPUT_ID}, '${ZONE_CANARY}', 2026111199, 'poison.${ZONE_CANARY}.', 86400, 'ALIAS', 'target.invalid.');"

log_pass "Poisoned transaction '${POISON_TID}' inserted into output queue."

step "Test 3: Publish urgent record to independent victim zone"
ndcli --quiet create rr "urgent.${ZONE_VICTIM}." A 192.0.2.1

# Verify that the victim record is queued behind the poisoned transaction
QUEUED_VICTIM=$(sql_command "SELECT COUNT(*) FROM outputupdate WHERE zone_name='${ZONE_VICTIM}';")
assert_not_empty "Victim update is queued in database" "$QUEUED_VICTIM"

step "Test 4: Demonstrate Head-of-Line Blocking (victim record is frozen and NOT published)"
VICTIM_PUBLISHED=false
for _ in $(seq 1 10); do
    VICTIM_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "urgent.${ZONE_VICTIM}." A +short)
    if [ "$VICTIM_ANS" = "192.0.2.1" ]; then
        VICTIM_PUBLISHED=true
        break
    fi
    sleep 1
done

assert_equals "Victim zone is blocked from publishing due to head-of-line stall" "false" "$VICTIM_PUBLISHED"
log_pass "Confirmed: Poisoned transaction at queue head blocks independent zone updates."

step "Test 5: Operator Recovery - Quarantine/remove poisoned transaction from queue"
sql_command "DELETE FROM outputupdate WHERE transaction='${POISON_TID}';"
log_pass "Poisoned transaction quarantined and deleted by operator."

step "Test 6: Verify queue recovery - victim zone drains and publishes immediately"
RECOVERED=false
for _ in $(seq 1 15); do
    RECOVER_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "urgent.${ZONE_VICTIM}." A +short)
    if [ "$RECOVER_ANS" = "192.0.2.1" ]; then
        RECOVERED=true
        break
    fi
    sleep 1
done

assert_equals "Victim record published to PowerDNS after unblocking queue" "true" "$RECOVERED"

step "Cleanup"
cleanup_hol_test

log_pass "Queue Head-of-Line blocking and recovery test completed successfully."
