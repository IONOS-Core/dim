#!/bin/bash
#
# suites/test-records-wildcard-dnssec.sh
# End-to-end test verifying Wildcard records (*.zone) and DNSSEC synthesis in PowerDNS.
# Proves that:
#   1. Wildcard A records can be created in DNSSEC-signed zones.
#   2. PowerDNS dynamically synthesizes responses and RRSIGs for unallocated subdomains.
#   3. Specific records (e.g. api.zone) cleanly override the wildcard.
#   4. Cryptographic integrity of wildcard synthesis is verified against PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


step "Clean up previous runs"
delete_zone_all_views wild-zone.com

step "Create DNSSEC-signed zone: wild-zone.com"
ndcli create zone wild-zone.com
ensure_output_sync wild-zone.com
ndcli modify zone wild-zone.com dnssec enable 8 ksk 2048 zsk 1024

step "Create Wildcard A record (*.wild-zone.com)"
ndcli create rr "*.wild-zone.com." A 10.9.8.7 --only-forward -q

step "Create specific override A record (api.wild-zone.com)"
ndcli create rr "api.wild-zone.com." A 10.9.8.99 --only-forward -q

step "Wait for PowerDNS to receive signed wildcard zone"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "api.wild-zone.com." A +noall +answer | grep -q 10.9.8.99; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive signed wildcard zone."
fi
log_pass "PowerDNS received signed wildcard zone."

step "Test 1: Query unallocated subdomain (should match wildcard 10.9.8.7)"
RANDOM_SUB="client-$(date +%s)"
QUERY_HOST="${RANDOM_SUB}.wild-zone.com."
WILD_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$QUERY_HOST" A +short)
echo "  Query: $QUERY_HOST -> Answer: $WILD_ANS"

if [ "$WILD_ANS" != 10.9.8.7 ]; then
    log_fail "Wildcard synthesis failed! Expected 10.9.8.7, got '$WILD_ANS'"
fi
log_pass "Wildcard successfully synthesized answer for unallocated subdomain."

step "Test 2: Query specific override record (api.wild-zone.com must return 10.9.8.99)"
SPECIFIC_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "api.wild-zone.com." A +short)
echo "  Query: api.wild-zone.com. -> Answer: $SPECIFIC_ANS"

if [ "$SPECIFIC_ANS" != 10.9.8.99 ]; then
    log_fail "Specific override failed! Expected 10.9.8.99, got '$SPECIFIC_ANS'"
fi
log_pass "Specific record cleanly overrides wildcard."

step "Test 3: Verify DNSSEC cryptographic validity on wildcard zone"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" wild-zone.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC cryptographic validation failed on wildcard zone!"
fi
log_pass "DNSSEC signatures on wildcard zone verified cryptographically."

step "Cleanup"
delete_zone_all_views wild-zone.com

log_pass "Test records-wildcard-dnssec completed successfully."
