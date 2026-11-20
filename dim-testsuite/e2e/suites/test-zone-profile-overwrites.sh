#!/bin/bash
#
# suites/test-zone-profile-overwrites.sh
# E2E Test: Explicit SOA timer overrides, serial progression, validation guards, and PowerDNS delivery
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="soa-overwrites-test.com"

cleanup_soa() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_soa

step "Create zone and capture baseline SOA parameters"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

BASELINE_SOA=$(ndcli list zone "$ZONE" -H | grep "SOA")
INITIAL_SERIAL=$(echo "$BASELINE_SOA" | awk '{print $7}')
INITIAL_TTL=$(echo "$BASELINE_SOA" | awk '{print $3}')
echo "  Baseline: Serial=$INITIAL_SERIAL, TTL=$INITIAL_TTL"

step "Test 1: Override SOA timers (refresh 3600, retry 900, ttl 300)"
ndcli modify zone "$ZONE" set refresh 3600 retry 900 ttl 300

UPDATED_SOA=$(ndcli list zone "$ZONE" -H | grep "SOA")
UPDATED_SERIAL=$(echo "$UPDATED_SOA" | awk '{print $7}')
UPDATED_TTL=$(echo "$UPDATED_SOA" | awk '{print $3}')
echo "  Updated:  Serial=$UPDATED_SERIAL, TTL=$UPDATED_TTL"

# Assert TTL was updated to 300
assert_equals "Zone TTL updated to 300" "300" "$UPDATED_TTL" "TTL"

# Assert serial incremented
SERIAL_BUMPED=false
if [ "$UPDATED_SERIAL" -gt "$INITIAL_SERIAL" ]; then
    SERIAL_BUMPED=true
fi
assert_equals "SOA serial incremented after timer override" "true" "$SERIAL_BUMPED"

# Assert new refresh and retry are stored
assert_contains "SOA contains updated refresh 3600" "3600" "$UPDATED_SOA"
assert_contains "SOA contains updated retry 900" "900" "$UPDATED_SOA"

step "Test 2: Validation Guard - Malformed primary nameserver rejected"
set +e
MALFORMED_PRIMARY=$(ndcli modify zone "$ZONE" set primary "bad..nameserver" 2>&1)
EXIT_PRIMARY=$?
set -e

assert_exit_code "Malformed primary NS rejected" 1 "$EXIT_PRIMARY" "$MALFORMED_PRIMARY"
assert_contains "Primary validation error message" "Invalid primary" "$MALFORMED_PRIMARY"

step "Wait for PowerDNS to receive updated SOA parameters"
SYNCED=false
PDNS_SOA=""
for _ in $(seq 1 20); do
    PDNS_SOA=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +short)
    if echo "$PDNS_SOA" | grep -q "3600 900"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS serves updated SOA timers" "true" "$SYNCED"

# Verify PowerDNS SOA TTL
PDNS_TTL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +noall +answer | awk '{print $2}')
assert_equals "PowerDNS serves updated SOA TTL" "300" "$PDNS_TTL" "TTL"

step "Cleanup"
cleanup_soa

log_pass "SOA timer overrides and PowerDNS delivery test passed."
