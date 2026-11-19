#!/bin/bash
#
# suites/test-core-transaction-retry-operational-error.sh
# End-to-end test validating database transaction retry, concurrency safety,
# and OperationalError handling under high-load conditions.
#
# Background:
# Under concurrent rr_create and rr_delete operations in reverse zones (in-addr.arpa),
# transactions encounter database lock waits or deadlocks. In Python 3, inspecting
# e.message in retryable_transaction raised:
#   AttributeError: 'OperationalError' object has no attribute 'message'
# which broke retries and masked genuine database errors with JSON-RPC error code 1.
#
# E2E Guarantees Tested:
# 1. High-concurrency parallel create operations complete without lock crashes.
# 2. High-concurrency mixed create and delete operations in forward & reverse zones succeed.
# 3. No client or worker ever receives "'OperationalError' object has no attribute 'message'".
# 4. Negative test: Error messages return proper domain/validation errors without leaking Python AttributeErrors.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh disable=SC1091
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="txn-retry-e2e.com"
REV_ZONE="222.168.192.in-addr.arpa"
CONTAINER="192.168.0.0/16"
POOL="txn-retry-pool"
SUBNET="192.168.222.0/24"
CONCURRENT_WORKERS=10

LOG_DIR=$(mktemp -d /tmp/txn-retry-XXXXXX)

cleanup_txn_test() {
    rm -rf "$LOG_DIR"
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    if ndcli show pool "$POOL" >/dev/null 2>&1; then
        if ndcli list pool "$POOL" subnets -H 2>/dev/null | grep -q "$SUBNET"; then
            ndcli -q modify pool "$POOL" remove subnet "$SUBNET"
        fi
        ndcli -q delete pool "$POOL"
    fi
    if ndcli show container "$CONTAINER" >/dev/null 2>&1; then
        ndcli -q delete container "$CONTAINER"
    fi
}

trap cleanup_txn_test EXIT

step "Clean up previous runs"
cleanup_txn_test

step "Initialize test zone and IPAM topology"
ndcli -q create container "$CONTAINER"
ndcli -q create pool "$POOL"
ndcli -q modify pool "$POOL" add subnet "$SUBNET"
ndcli -q create zone "$ZONE"
ensure_output_sync "$ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: High-concurrency parallel RR creation"
PIDS=()

for i in $(seq 1 "$CONCURRENT_WORKERS"); do
    (
        IP="192.168.222.$((i + 10))"
        PTR_NAME="$((i + 10)).222.168.192.in-addr.arpa."
        HOST="worker${i}.${ZONE}."

        # Create A record
        ndcli create rr "$HOST" A "$IP" >"${LOG_DIR}/worker_${i}_create.log" 2>&1 || exit 1
        # Create reverse PTR record
        ndcli create rr "$PTR_NAME" PTR "$HOST" >>"${LOG_DIR}/worker_${i}_create.log" 2>&1 || exit 1
    ) &
    PIDS+=("$!")
done

ERRORS=0
for pid in "${PIDS[@]}"; do
    if ! wait "$pid"; then
        ERRORS=$((ERRORS + 1))
    fi
done

assert_equals "All concurrent RR allocations succeeded without lock abort" "0" "$ERRORS" "Worker Errors"

if grep -q -rn "has no attribute 'message'" "$LOG_DIR"; then
    log_fail "AttributeError detected in worker logs!"
else
    log_pass "Zero occurrences of AttributeError in worker logs"
fi

step "Verify all records exist in DIM"
TOTAL_A_RECORDS=$(ndcli list rrs "*.${ZONE}." A -H | awk '/worker/{c++} END{print c+0}')
assert_equals "Total concurrent A records created" "$CONCURRENT_WORKERS" "$TOTAL_A_RECORDS" "Count"

TOTAL_PTR_RECORDS=$(ndcli list rrs "*.${REV_ZONE}." PTR -H | awk -v z="${ZONE}." '$0 ~ z {c++} END{print c+0}')
assert_equals "Total concurrent PTR records created" "$CONCURRENT_WORKERS" "$TOTAL_PTR_RECORDS" "Count"

step "Test 2: High-concurrency mixed create and delete operations"
PIDS=()
for i in $(seq 1 "$CONCURRENT_WORKERS"); do
    (
        IP="192.168.222.$((i + 10))"
        PTR_NAME="$((i + 10)).222.168.192.in-addr.arpa."
        HOST="worker${i}.${ZONE}."
        NEW_HOST="newworker${i}.${ZONE}."
        NEW_IP="192.168.222.$((i + 50))"
        NEW_PTR="$((i + 50)).222.168.192.in-addr.arpa."

        # Delete previously created records
        ndcli delete rr "$PTR_NAME" PTR "$HOST" >"${LOG_DIR}/mixed_${i}.log" 2>&1 || exit 1
        ndcli delete rr "$HOST" A "$IP" >>"${LOG_DIR}/mixed_${i}.log" 2>&1 || exit 1

        # Simultaneously create new records
        ndcli create rr "$NEW_HOST" A "$NEW_IP" >>"${LOG_DIR}/mixed_${i}.log" 2>&1 || exit 1
        ndcli create rr "$NEW_PTR" PTR "$NEW_HOST" >>"${LOG_DIR}/mixed_${i}.log" 2>&1 || exit 1
    ) &
    PIDS+=("$!")
done

ERRORS=0
for pid in "${PIDS[@]}"; do
    if ! wait "$pid"; then
        ERRORS=$((ERRORS + 1))
    fi
done

assert_equals "All concurrent mixed create/delete operations completed successfully" "0" "$ERRORS" "Worker Errors"

if grep -q -rn "has no attribute 'message'" "$LOG_DIR"; then
    log_fail "AttributeError detected in mixed worker logs!"
else
    log_pass "Zero occurrences of AttributeError in mixed concurrent operations"
fi

step "Test 3: Negative Test - Invalid operation error message integrity"
set +e
ERR_OUT=$(ndcli create rr "invalid..${ZONE}." A "999.999.999.999" 2>&1)
ERR_EXIT=$?
set -e

assert_exit_code "Invalid RR creation fails with non-zero exit code" 1 "$ERR_EXIT" "$ERR_OUT"

if echo "$ERR_OUT" | grep -qi "AttributeError\|has no attribute 'message'"; then
    log_fail "DIM leaked internal AttributeError to client: $ERR_OUT"
else
    log_pass "Invalid operation returned clean client error without internal AttributeError leak"
fi
