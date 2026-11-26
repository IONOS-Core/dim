#!/bin/bash
#
# suites/test-core-axfr-sync.sh
# E2E Test: Authoritative Zone Transfer (AXFR) completeness, DNSSEC payload, and serial consistency
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_axfr() {
    delete_zone_all_views axfr-sync-test.com
}

step "Clean up previous runs"
cleanup_axfr

step "Create DNSSEC-enabled zone with diverse record types"
ndcli create zone axfr-sync-test.com -q
ensure_output_sync axfr-sync-test.com
ndcli create rr web.axfr-sync-test.com. A 10.0.1.10 -q
ndcli create rr web.axfr-sync-test.com. AAAA 2001:db8::10 -q
ndcli create rr mail.axfr-sync-test.com. MX 10 "mailgw.example.org." -q
ndcli create rr axfr-sync-test.com. TXT "axfr verification payload" -q
ndcli modify zone axfr-sync-test.com dnssec enable 8 ksk 2048 zsk 1024 -q

step "Wait for PowerDNS to receive signed zone"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" axfr-sync-test.com. SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "Zone sync to PowerDNS" "true" "$SYNCED"

step "Test 1: Execute AXFR transfer against PowerDNS"
AXFR_OUTPUT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" axfr-sync-test.com. AXFR)
echo "  AXFR record count: $(echo "$AXFR_OUTPUT" | grep -v '^;' | grep -c -v '^$')"

# AXFR RFC 5936 rule: First and last records MUST be the zone SOA
FIRST_RECORD_TYPE=$(echo "$AXFR_OUTPUT" | grep -v '^;' | grep -v '^$' | head -n1 | awk '{print $4}')
LAST_RECORD_TYPE=$(echo "$AXFR_OUTPUT" | grep -v '^;' | grep -v '^$' | tail -n1 | awk '{print $4}')

assert_equals "AXFR opening record type" "SOA" "$FIRST_RECORD_TYPE"
assert_equals "AXFR closing record type" "SOA" "$LAST_RECORD_TYPE"

# Verify all created records exist in AXFR transfer
assert_contains "AXFR payload contains A record" "10.0.1.10" "$AXFR_OUTPUT"
assert_contains "AXFR payload contains AAAA record" "2001:db8::10" "$AXFR_OUTPUT"
assert_contains "AXFR payload contains MX record" "mailgw.example.org." "$AXFR_OUTPUT"
assert_contains "AXFR payload contains TXT record" "axfr verification payload" "$AXFR_OUTPUT"
assert_contains "AXFR payload contains DNSKEY" "DNSKEY" "$AXFR_OUTPUT"
assert_contains "AXFR payload contains RRSIG" "RRSIG" "$AXFR_OUTPUT"

# Extract initial SOA serial from AXFR
SERIAL_INITIAL=$(echo "$AXFR_OUTPUT" | grep -v '^;' | awk '$4 == "SOA" {print $7}' | head -n1)
echo "  Initial AXFR SOA serial: $SERIAL_INITIAL"
assert_not_empty "Initial SOA serial" "$SERIAL_INITIAL"

step "Test 2: Mutate zone and assert SOA serial increment in next AXFR"
ndcli create rr api.axfr-sync-test.com. A 10.0.1.99 -q

sleep 2
AXFR_SECOND=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" axfr-sync-test.com. AXFR)
SERIAL_SECOND=$(echo "$AXFR_SECOND" | grep -v '^;' | awk '$4 == "SOA" {print $7}' | head -n1)
echo "  Updated AXFR SOA serial: $SERIAL_SECOND"

assert_contains "Updated AXFR contains new record" "10.0.1.99" "$AXFR_SECOND"

SERIAL_INCREASED=false
if [ "$SERIAL_SECOND" -gt "$SERIAL_INITIAL" ]; then
    SERIAL_INCREASED=true
fi
assert_equals "SOA serial strictly incremented upon zone mutation" "true" "$SERIAL_INCREASED"

step "Cleanup"
cleanup_axfr

log_pass "AXFR zone transfer sync test passed."
