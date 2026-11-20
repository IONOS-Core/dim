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

POOL="concur-pool"
CONTAINER="192.168.0.0/16"
SUBNET="192.168.235.0/28"
ZONE="concur-test.com"
REV_ZONE="235.168.192.in-addr.arpa"
CONCURRENT_WORKERS=10

cleanup_concur() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli -q modify pool "$POOL" remove subnet "$SUBNET" 2>/dev/null || true
    ndcli -q delete pool "$POOL" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_concur

step "Create IPAM Container, Pool, and /28 Subnet ($SUBNET)"
ndcli -q create container "$CONTAINER"
ndcli -q create pool "$POOL"
ndcli -q modify pool "$POOL" add subnet "$SUBNET"
ndcli -q create zone "$ZONE"
ensure_output_sync "$ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: Launch $CONCURRENT_WORKERS parallel worker processes allocating from the same pool"
PIDS=()
ERRORS=0

for i in $(seq 1 "$CONCURRENT_WORKERS"); do
    (
        ndcli -q create rr "worker${i}.${ZONE}." from "$POOL"
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
TOTAL_A_RECORDS=$(ndcli list rrs "*.${ZONE}." A -H | grep "worker" | wc -l)
assert_equals "Total concurrent A records created" "$CONCURRENT_WORKERS" "$TOTAL_A_RECORDS" "Count"

UNIQUE_IPS=$(ndcli list rrs "*.${ZONE}." A -H | awk '{print $5}' | sort -u | wc -l)
assert_equals "All concurrently allocated IPs are strictly unique" "$CONCURRENT_WORKERS" "$UNIQUE_IPS" "Count"

# shellcheck disable=SC2126
TOTAL_PTR_RECORDS=$(ndcli list rrs "*.${REV_ZONE}." PTR -H | grep "${ZONE}." | wc -l)
assert_equals "Total concurrent PTR records created" "$CONCURRENT_WORKERS" "$TOTAL_PTR_RECORDS" "Count"

step "Wait for PowerDNS to receive all concurrent records"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_COUNT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "worker${CONCURRENT_WORKERS}.${ZONE}." A +short)
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
