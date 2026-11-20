#!/bin/bash
#
# suites/test-records-srv-validation.sh
# E2E Test: RFC 2782 SRV record syntax, 16-bit uint boundary validation (priority, weight, port), and PowerDNS delivery
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="srv-validation-test.com"
SRV_NAME="_sip._tcp.${ZONE}."
TARGET_HOST="sipserver.${ZONE}."

cleanup_srv() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_srv

step "Create forward zone"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

step "Test 1: Valid SRV record creation"
ndcli --quiet create rr "$SRV_NAME" SRV 10 60 5060 "$TARGET_HOST"

SRV_VAL=$(ndcli list rrs "$SRV_NAME" SRV -H | awk '{print $5, $6, $7, $8}')
echo "  SRV value in DIM: $SRV_VAL"
assert_equals "SRV record value matches specification" "10 60 5060 ${TARGET_HOST}" "$SRV_VAL" "SRV Value"

step "Test 2: RFC 2782 16-bit boundary validations - Port > 65535 rejected"
set +e
PORT_ERR=$(ndcli create rr "$SRV_NAME" SRV 10 60 70000 "$TARGET_HOST" 2>&1)
EXIT_PORT=$?
set -e

assert_exit_code "Port exceeding 65535 rejected" 1 "$EXIT_PORT" "$PORT_ERR"
assert_contains "Port error message" "Invalid port: 70000" "$PORT_ERR"

step "Test 3: RFC 2782 16-bit boundary validations - Priority > 65535 rejected"
set +e
PRIO_ERR=$(ndcli create rr "$SRV_NAME" SRV 66000 60 5060 "$TARGET_HOST" 2>&1)
EXIT_PRIO=$?
set -e

assert_exit_code "Priority exceeding 65535 rejected" 1 "$EXIT_PRIO" "$PRIO_ERR"
assert_contains "Priority error message" "Invalid priority: 66000" "$PRIO_ERR"

step "Test 4: Boundary extremes (Priority=0, Weight=0, Port=65535) accepted"
ndcli --quiet create rr "_extreme._tcp.${ZONE}." SRV 0 0 65535 "$TARGET_HOST"

EXTREME_VAL=$(ndcli list rrs "_extreme._tcp.${ZONE}." SRV -H | awk '{print $5, $6, $7, $8}')
assert_equals "Extreme SRV boundaries accepted" "0 0 65535 ${TARGET_HOST}" "$EXTREME_VAL" "SRV Value"

step "Wait for PowerDNS to receive and serve SRV records"
SYNCED=false
PDNS_SRV=""
for _ in $(seq 1 20); do
    PDNS_SRV=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$SRV_NAME" SRV +short)
    if [ "$PDNS_SRV" = "10 60 5060 ${TARGET_HOST}" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS serves authoritative SRV record" "true" "$SYNCED"

step "Cleanup"
cleanup_srv

log_pass "RFC 2782 SRV record validation test passed."
