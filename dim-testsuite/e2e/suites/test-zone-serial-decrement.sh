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


cleanup_dec() {
    delete_zone_all_views serial-decrement-test.com
}

step "Clean up previous runs"
cleanup_dec

step "Create zone and set high baseline serial (2026111050)"
ndcli --quiet create zone serial-decrement-test.com
ensure_output_sync serial-decrement-test.com
ndcli modify zone serial-decrement-test.com set serial 2026111050

SERIAL_INIT=$(ndcli list zone serial-decrement-test.com -H | grep "SOA" | awk '{print $7}')
assert_equals "Baseline serial set to high value" 2026111050 "$SERIAL_INIT" "Serial"

step "Test 1: Decrement SOA serial to lower value (1000)"
ndcli modify zone serial-decrement-test.com set serial 1000

SERIAL_DECREMENTED=$(ndcli list zone serial-decrement-test.com -H | grep "SOA" | awk '{print $7}')
assert_equals "Serial decremented in DIM" 1000 "$SERIAL_DECREMENTED" "Serial"

step "Wait for PowerDNS to receive decremented serial"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_SERIAL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "serial-decrement-test.com." SOA +short | awk '{print $3}' || true)
    if [ "$PDNS_SERIAL" = 1000 ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received decremented serial sync" "true" "$SYNCED"
assert_equals "PowerDNS authoritative server reflects decremented serial" 1000 "$PDNS_SERIAL" "Serial"

step "Test 2: Assert normal +1 progression resumes from decremented baseline"
ndcli --quiet create rr "node1.serial-decrement-test.com." A 10.1.1.1 --only-forward

SERIAL_RESUMED=$(ndcli list zone serial-decrement-test.com -H | grep "SOA" | awk '{print $7}')
EXPECTED_NEXT=$((1000 + 1))
assert_equals "Serial resumes +1 increment from decremented baseline" "$EXPECTED_NEXT" "$SERIAL_RESUMED" "Serial"

step "Wait for PowerDNS to serve incremented serial ($EXPECTED_NEXT)"
SYNCED_RESUME=false
for _ in $(seq 1 20); do
    PDNS_RESUME=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "serial-decrement-test.com." SOA +short | awk '{print $3}' || true)
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
