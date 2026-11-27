#!/bin/bash
#
# suites/test-core-concurrency-locks.sh
# E2E Test: High-concurrency parallel writes, database lock contention, and IP collision safety
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

CONCURRENT_WORKERS=10

cleanup_concur() {
    delete_zone_all_views "concur-test.com"
    delete_zone_all_views "235.168.192.in-addr.arpa"
    ndcli -q modify pool concur-pool remove subnet 192.168.235.0/28 2>/dev/null || true
    ndcli -q delete pool concur-pool 2>/dev/null || true
    ndcli -q delete container 192.168.0.0/16 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_concur

step "Create IPAM Container, Pool, and /28 Subnet (192.168.235.0/28)"
ndcli -q create container 192.168.0.0/16
ndcli -q create pool concur-pool
ndcli -q modify pool concur-pool add subnet 192.168.235.0/28
ndcli -q create zone concur-test.com
ensure_output_sync "concur-test.com"
ensure_output_sync "235.168.192.in-addr.arpa"

step "Test 1: Launch $CONCURRENT_WORKERS parallel worker processes allocating from the same pool"
PIDS=()
ERRORS=0

for i in $(seq 1 "$CONCURRENT_WORKERS"); do
    (
        ndcli -u admin -p admin -q create rr "worker${i}.concur-test.com." from concur-pool
    ) &
    PIDS+=("$!")
done

# Wait for all background workers to complete and collect exit codes
for pid in "${PIDS[@]}"; do
    if ! wait "$pid"; then
        ERRORS=$((ERRORS + 1))
    fi
done

assert_equals "All concurrent allocations succeeded without lock failure" "0" "$ERRORS" "Worker Errors"

step "Test 2: Assert zero IP collisions among concurrent allocations"
# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
TOTAL_A_RECORDS=$(ndcli list rrs "*.concur-test.com." A -H | grep "worker" | wc -l)
assert_equals "Total concurrent A records created" "$CONCURRENT_WORKERS" "$TOTAL_A_RECORDS" "Count"

UNIQUE_IPS=$(ndcli list rrs "*.concur-test.com." A -H | awk '{print $5}' | sort -u | wc -l)
assert_equals "All concurrently allocated IPs are strictly unique" "$CONCURRENT_WORKERS" "$UNIQUE_IPS" "Count"

# shellcheck disable=SC2126
TOTAL_PTR_RECORDS=$(ndcli list rrs "*.235.168.192.in-addr.arpa." PTR -H | grep "concur-test.com." | wc -l)
assert_equals "Total concurrent PTR records created" "$CONCURRENT_WORKERS" "$TOTAL_PTR_RECORDS" "Count"

step "Wait for PowerDNS to receive all concurrent records"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_COUNT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "worker${CONCURRENT_WORKERS}.concur-test.com." A +short)
    if [ -n "$PDNS_COUNT" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received concurrent records" "true" "$SYNCED"

step "Cleanup"
cleanup_concur

log_pass "Concurrency locking and collision safety test passed."
