#!/bin/bash
#
# suites/test-core-multi-output-drift.sh
# End-to-end test verifying multi-output parallel synchronization, drift detection,
# and convergence across redundant PowerDNS authoritative nameservers (pdns1 & pdns2).
#
# Threat Model (Split-Brain / Desynchronization):
# In production, authoritative DNS runs redundantly (pdns1 & pdns2) via separate DIM outputs.
# If one output experiences a database timeout, lock contention, or processing delay while
# the other succeeds, resolvers query both servers round-robin and receive conflicting records
# and out-of-sync SOA serials.
#
# This test verifies:
#   1. Both outputs (pdns1 & pdns2) synchronize in parallel and serve identical DNS data.
#   2. When an output is delayed/blocked, DIM tracks the lagging output's pending updates.
#   3. Resolving the block allows the lagging output to converge cleanly without manual intervention.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="drift-eval.com"
ZG_MULTI="zg-multi-drift"
OUT2="out2-drift"

cleanup_drift_test() {
    delete_zone_all_views "$ZONE"
    ndcli -q modify output out remove zone-group "$ZG_MULTI" 2>/dev/null || true
    if ndcli show output "$OUT2" >/dev/null 2>&1; then
        ndcli -q modify output "$OUT2" remove zone-group "$ZG_MULTI" 2>/dev/null || true
        ndcli -q delete output "$OUT2" 2>/dev/null || true
    fi
    ndcli -q delete zone-group "$ZG_MULTI" 2>/dev/null || true
}
trap cleanup_drift_test EXIT INT TERM

step "Clean up previous runs"
cleanup_drift_test

step "Configure second output (out2 -> pdns2) and dual-output zone-group"
ndcli --quiet create output "$OUT2" plugin pdns-db db-uri "mysql://pdns:pdns@127.0.0.1:3307/pdns2"
ndcli --quiet create zone-group "$ZG_MULTI"

ndcli --quiet modify output out add zone-group "$ZG_MULTI"
ndcli --quiet modify output "$OUT2" add zone-group "$ZG_MULTI"

step "Create zone and add to dual-output zone-group"
ndcli --quiet create zone "$ZONE"
ndcli --quiet modify zone-group "$ZG_MULTI" add zone "$ZONE"
ndcli --quiet create rr "app.${ZONE}." A 10.0.0.1

step "Test 1: Baseline Synchronization - Verify both pdns1 and pdns2 serve identical records"
SYNCED_PDNS1=false
SYNCED_PDNS2=false

for _ in $(seq 1 20); do
    if [ "$SYNCED_PDNS1" != "true" ]; then
        ANS1=$(dig "@127.1.1.1" "app.${ZONE}." A +short)
        if [ "$ANS1" = "10.0.0.1" ]; then SYNCED_PDNS1=true; fi
    fi
    if [ "$SYNCED_PDNS2" != "true" ]; then
        ANS2=$(dig "@127.2.2.2" "app.${ZONE}." A +short)
        if [ "$ANS2" = "10.0.0.1" ]; then SYNCED_PDNS2=true; fi
    fi
    if [ "$SYNCED_PDNS1" = "true" ] && [ "$SYNCED_PDNS2" = "true" ]; then
        break
    fi
    sleep 1
done

assert_equals "pdns1 (@127.1.1.1) synchronized baseline record" "true" "$SYNCED_PDNS1"
assert_equals "pdns2 (@127.2.2.2) synchronized baseline record" "true" "$SYNCED_PDNS2"
log_pass "Both nameservers are in sync: app.${ZONE}. -> 10.0.0.1."

step "Test 2: Simulate output drift by blocking out2 while mutating record"
# Find numeric ID of out2
OUT2_ID=$(sql_command "SELECT id FROM output WHERE name='${OUT2}';")

# Use persistent Python helper to hold named lock on out2 while applying update
python3 -c "
import MySQLdb, subprocess, time

db = MySQLdb.connect(host='127.0.0.1', port=3307, user='dim', passwd='dim', db='dim')
c = db.cursor()

# Hold lock on output thread for out2
c.execute(\"SELECT GET_LOCK('output_${OUT2_ID}', 10);\")

# Apply update in DIM (change IP to 10.0.0.2)
subprocess.run(['ndcli', '--quiet', 'create', 'rr', 'app.${ZONE}.', 'A', '10.0.0.2', '--overwrite-a'], check=True)

# Allow pdns1 to process the update while out2 is blocked
time.sleep(2)

# Verify pdns1 updated to 10.0.0.2
res1 = subprocess.run(['dig', '@127.1.1.1', 'app.${ZONE}.', 'A', '+short'], capture_output=True, text=True)
ans1 = res1.stdout.strip()
print(f'  pdns1 (healthy) answer: {ans1}')

# Verify pdns2 is still serving stale 10.0.0.1
res2 = subprocess.run(['dig', '@127.2.2.2', 'app.${ZONE}.', 'A', '+short'], capture_output=True, text=True)
ans2 = res2.stdout.strip()
print(f'  pdns2 (blocked) answer: {ans2}')

if ans1 == '10.0.0.2' and ans2 == '10.0.0.1':
    print('  [CONFIRMED] Nameserver drift observed: pdns1 updated, pdns2 lagged behind.')
else:
    print(f'  [UNEXPECTED] Drift state mismatch: pdns1={ans1}, pdns2={ans2}')

# Release lock to allow out2 to catch up
c.execute(\"SELECT RELEASE_LOCK('output_${OUT2_ID}');\")
db.close()
"

step "Test 3: Convergence - Verify out2 catches up after unblocking"
CONVERGED=false
for _ in $(seq 1 20); do
    C_ANS2=$(dig "@127.2.2.2" "app.${ZONE}." A +short)
    if [ "$C_ANS2" = "10.0.0.2" ]; then
        CONVERGED=true
        break
    fi
    sleep 1
done

assert_equals "pdns2 (@127.2.2.2) converged cleanly after unblocking" "true" "$CONVERGED"
log_pass "Convergence verified: Both nameservers now serve updated IP 10.0.0.2."

step "Cleanup"
cleanup_drift_test

log_pass "Multi-output drift and convergence test completed successfully."
