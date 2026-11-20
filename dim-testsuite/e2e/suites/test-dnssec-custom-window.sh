#!/bin/bash
#
# suites/test-dnssec-custom-window.sh
# E2E Test: Zone-level custom validity window (dnssec_validity_window) and RFC 6781 inception skew (dnssec_validity_skew)
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="custom-window-eval.com"
CUSTOM_WINDOW=604800   # 7 days in seconds
CUSTOM_SKEW=7200       # 2 hours backdating in seconds

cleanup_cwin() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_cwin

step "Create zone and set custom validity window (7d) and inception skew (2h)"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet modify zone "$ZONE" set attrs "dnssec_validity_window:${CUSTOM_WINDOW}" "dnssec_validity_skew:${CUSTOM_SKEW}"
ndcli --quiet modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
ndcli --quiet create rr "host1.${ZONE}." A 10.88.1.1

# Verify attributes in DIM
SKEW_DIM=$(ndcli show zone "$ZONE" | grep '^dnssec_validity_skew:' | cut -d':' -f2)
WINDOW_DIM=$(ndcli show zone "$ZONE" | grep '^dnssec_validity_window:' | cut -d':' -f2)

assert_equals "Custom skew configured in DIM" "$CUSTOM_SKEW" "$SKEW_DIM" "Seconds"
assert_equals "Custom window configured in DIM" "$CUSTOM_WINDOW" "$WINDOW_DIM" "Seconds"

step "Wait for PowerDNS to receive signed zone with custom validity window"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received signed zone" "true" "$SYNCED"

step "Test 1: Verify custom 2-hour backdating and 7-day validity duration in PowerDNS"
RRSIG_LINE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep "RRSIG")
EXP_RAW=$(echo "$RRSIG_LINE" | awk '{print $9}')
INC_RAW=$(echo "$RRSIG_LINE" | awk '{print $10}')

echo "  RRSIG Inception:  $INC_RAW"
echo "  RRSIG Expiration: $EXP_RAW"

# Calculate total duration between inception and expiration
TOTAL_DURATION=$(python3 -c "
from datetime import datetime, timezone
inc = datetime.strptime('$INC_RAW', '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
exp = datetime.strptime('$EXP_RAW', '%Y%m%d%H%M%S').replace(tzinfo=timezone.utc)
print(int((exp - inc).total_seconds()))
")

EXPECTED_TOTAL=$((CUSTOM_WINDOW + CUSTOM_SKEW))
echo "  Total RRSIG duration: $TOTAL_DURATION seconds (Expected: $EXPECTED_TOTAL)"

assert_equals "RRSIG total duration matches window + skew" "$EXPECTED_TOTAL" "$TOTAL_DURATION" "Duration Seconds"

step "Test 2: Verify Cryptographic and Inception Plausibility via verify-dnssec-zone.py"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed for custom window zone $ZONE!"
fi
log_pass "Zone signatures verified cryptographically with custom 2h backdating."

step "Cleanup"
cleanup_cwin

log_pass "Custom validity window and inception skew test passed."
