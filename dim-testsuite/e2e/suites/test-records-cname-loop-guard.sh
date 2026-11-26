#!/bin/bash
#
# suites/test-records-cname-loop-guard.sh
# End-to-end test proving the danger of unvalidated circular CNAME chains (RFC 1034 / RFC 2181).
#
# Threat Model:
# If DIM permits creating circular CNAME references (e.g. a -> b and b -> a, or a -> b -> c -> a),
# PowerDNS exhausts recursion limits and returns SERVFAIL to clients and validating resolvers,
# causing immediate service outages for affected domains.
#
# Expected Safety Guard:
# DIM should detect circular CNAME dependencies upon creation and reject them cleanly with
# an error (e.g. 'Circular CNAME reference detected'), preventing corrupted zones in PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_loop() {
    delete_zone_all_views cname-loop-eval.com
}
trap cleanup_loop EXIT INT TERM

step "Clean up previous runs"
cleanup_loop

step "Create zone and enable output synchronization"
ndcli --quiet create zone cname-loop-eval.com
ensure_output_sync cname-loop-eval.com

step "Test 1: Create initial CNAME pointing from a to b"
ndcli --quiet create rr "a.cname-loop-eval.com." CNAME "b.cname-loop-eval.com."
log_pass "Initial CNAME a -> b created."

step "Test 2: Prove whether DIM blocks or allows direct circular CNAME (b -> a)"
set +e
LOOP_OUT=$(ndcli create rr "b.cname-loop-eval.com." CNAME "a.cname-loop-eval.com." 2>&1)
LOOP_EXIT=$?
set -e

echo "  DIM response for circular CNAME creation:"
echo "  $LOOP_OUT"

# Check if DIM rejected the circular CNAME (Expected safety guard)
if [ "$LOOP_EXIT" -ne 0 ]; then
    log_pass "DIM successfully rejected circular CNAME reference."
else
    # Current behavior: DIM allows the loop, creating a broken zone in PowerDNS
    step "Wait for PowerDNS to receive circular CNAME records"
    SYNCED=false
    for _ in $(seq 1 20); do
        PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "a.cname-loop-eval.com." CNAME +short)
        if [ "$PDNS_ANS" = "b.cname-loop-eval.com." ]; then
            SYNCED=true
            break
        fi
        sleep 1
    done
    assert_equals "PowerDNS received CNAME records" "true" "$SYNCED"

    step "Test 3: Querying resolution of circular CNAME in PowerDNS results in SERVFAIL"
    QUERY_STATUS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "a.cname-loop-eval.com." A | grep "status:" | awk '{print $6}' | tr -d ',')
    echo "  PowerDNS response status for circular CNAME: $QUERY_STATUS"

    assert_equals "PowerDNS returns SERVFAIL on circular CNAME loop" "SERVFAIL" "$QUERY_STATUS" "DNS Status"

    log_fail "DEFECT DEMONSTRATED: DIM permitted circular CNAME (a -> b -> a) which causes SERVFAIL in PowerDNS!"
fi

step "Cleanup"
cleanup_loop
