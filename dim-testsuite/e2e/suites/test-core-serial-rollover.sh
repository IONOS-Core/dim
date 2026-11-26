#!/bin/bash
#
# suites/test-core-serial-rollover.sh
# E2E Test: SOA serial number progression, manual overrides, and 32-bit boundary constraints
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_serial() {
    ndcli modify zone serial-eval-test.com set serial 2026111001 2>/dev/null || true
    delete_zone_all_views serial-eval-test.com
}

step "Clean up previous runs"
cleanup_serial

step "Create zone and verify initial date-based SOA serial format (YYYYMMDD01)"
ndcli --quiet create zone serial-eval-test.com
ensure_output_sync serial-eval-test.com

INITIAL_SERIAL=$(ndcli list zone serial-eval-test.com -H | grep "SOA" | awk '{print $7}')
echo "  Initial serial in DIM: $INITIAL_SERIAL"
assert_not_empty "Initial SOA serial" "$INITIAL_SERIAL" "Serial"

TODAY_PREFIX=$(date +%Y%m%d)
assert_contains "Serial starts with today's date" "$TODAY_PREFIX" "$INITIAL_SERIAL"

step "Test 1: Normal record mutation strictly increments serial by 1"
ndcli --quiet create rr "h1.serial-eval-test.com." A 10.1.1.1 --only-forward

SERIAL_AFTER_H1=$(ndcli list zone serial-eval-test.com -H | grep "SOA" | awk '{print $7}')
EXPECTED_NEXT=$((INITIAL_SERIAL + 1))
assert_equals "Serial incremented by 1 after mutation" "$EXPECTED_NEXT" "$SERIAL_AFTER_H1" "Serial"

step "Test 2: Two-digit day boundary crossing (e.g. .99 to .100)"
# Set serial to .99 boundary
TARGET_99="${TODAY_PREFIX}99"
ndcli modify zone serial-eval-test.com set serial "$TARGET_99"

SERIAL_99=$(ndcli list zone serial-eval-test.com -H | grep "SOA" | awk '{print $7}')
assert_equals "Serial updated to 99 boundary" "$TARGET_99" "$SERIAL_99" "Serial"

# Mutate zone: serial must cleanly increment to 100 without crashing
ndcli --quiet create rr "h2.serial-eval-test.com." A 10.1.1.2 --only-forward

SERIAL_100=$(ndcli list zone serial-eval-test.com -H | grep "SOA" | awk '{print $7}')
EXPECTED_100=$((TARGET_99 + 1))
assert_equals "Serial cleanly crosses 99 boundary to 100" "$EXPECTED_100" "$SERIAL_100" "Serial"

step "Wait for PowerDNS to receive updated serial ($EXPECTED_100)"
SYNCED=false
PDNS_SERIAL=""
for _ in $(seq 1 20); do
    PDNS_SERIAL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "serial-eval-test.com." SOA +short | awk '{print $3}')
    if [ "$PDNS_SERIAL" = "$EXPECTED_100" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received updated serial" "true" "$SYNCED"
assert_equals "PowerDNS authoritative server serves incremented serial" "$EXPECTED_100" "$PDNS_SERIAL" "Serial"

step "Test 3: Max 32-bit unsigned int boundary (RFC 1982 upper bound: 4294967295)"
MAX_UINT32="4294967295"
ndcli modify zone serial-eval-test.com set serial "$MAX_UINT32"

SERIAL_MAX=$(ndcli list zone serial-eval-test.com -H | grep "SOA" | awk '{print $7}')
assert_equals "Serial set to maximum 32-bit integer" "$MAX_UINT32" "$SERIAL_MAX" "Serial"

step "Cleanup"
cleanup_serial

log_pass "SOA serial progression and boundary test passed."
