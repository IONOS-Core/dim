#!/bin/bash
#
# suites/test-zone-serial-decrement.sh
# E2E Test: SOA serial number manual decrement, PowerDNS reflection, and resumption of +1 progression
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="serial-decrement-test.com"
INITIAL_HIGH="2026111050"
LOWER_TARGET="1000"

cleanup_dec() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_dec

step "Create zone and set high baseline serial ($INITIAL_HIGH)"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli modify zone "$ZONE" set serial "$INITIAL_HIGH"

SERIAL_INIT=$(ndcli list zone "$ZONE" -H | grep "SOA" | awk '{print $7}')
assert_equals "Baseline serial set to high value" "$INITIAL_HIGH" "$SERIAL_INIT" "Serial"

step "Test 1: Decrement SOA serial to lower value ($LOWER_TARGET)"
ndcli modify zone "$ZONE" set serial "$LOWER_TARGET"

SERIAL_DECREMENTED=$(ndcli list zone "$ZONE" -H | grep "SOA" | awk '{print $7}')
assert_equals "Serial decremented in DIM" "$LOWER_TARGET" "$SERIAL_DECREMENTED" "Serial"

step "Wait for PowerDNS to receive decremented serial"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_SERIAL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +short | awk '{print $3}' || true)
    if [ "$PDNS_SERIAL" = "$LOWER_TARGET" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received decremented serial sync" "true" "$SYNCED"
assert_equals "PowerDNS authoritative server reflects decremented serial" "$LOWER_TARGET" "$PDNS_SERIAL" "Serial"

step "Test 2: Assert normal +1 progression resumes from decremented baseline"
ndcli --quiet create rr "node1.${ZONE}." A 10.1.1.1 --only-forward

SERIAL_RESUMED=$(ndcli list zone "$ZONE" -H | grep "SOA" | awk '{print $7}')
EXPECTED_NEXT=$((LOWER_TARGET + 1))
assert_equals "Serial resumes +1 increment from decremented baseline" "$EXPECTED_NEXT" "$SERIAL_RESUMED" "Serial"

step "Wait for PowerDNS to serve incremented serial ($EXPECTED_NEXT)"
SYNCED_RESUME=false
for _ in $(seq 1 20); do
    PDNS_RESUME=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +short | awk '{print $3}' || true)
    if [ "$PDNS_RESUME" = "$EXPECTED_NEXT" ]; then
        SYNCED_RESUME=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received resumed serial sync" "true" "$SYNCED_RESUME"
assert_equals "PowerDNS serves incremented serial" "$EXPECTED_NEXT" "$PDNS_RESUME" "Serial"

step "Cleanup"
cleanup_dec

log_pass "SOA serial decrement and progression test passed."
