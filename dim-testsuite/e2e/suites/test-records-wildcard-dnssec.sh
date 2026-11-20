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

ZONE="wild-zone.com"
WILDCARD_IP="10.9.8.7"
SPECIFIC_IP="10.9.8.99"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"

step "Create DNSSEC-signed zone: $ZONE"
ndcli create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024

step "Create Wildcard A record (*.$ZONE)"
ndcli create rr "*.${ZONE}." A "$WILDCARD_IP" --only-forward -q

step "Create specific override A record (api.$ZONE)"
ndcli create rr "api.${ZONE}." A "$SPECIFIC_IP" --only-forward -q

step "Wait for PowerDNS to receive signed wildcard zone"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "api.${ZONE}." A +noall +answer | grep -q "$SPECIFIC_IP"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive signed wildcard zone."
fi
log_pass "PowerDNS received signed wildcard zone."

step "Test 1: Query unallocated subdomain (should match wildcard $WILDCARD_IP)"
RANDOM_SUB="client-$(date +%s)"
QUERY_HOST="${RANDOM_SUB}.${ZONE}."
WILD_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$QUERY_HOST" A +short)
echo "  Query: $QUERY_HOST -> Answer: $WILD_ANS"

if [ "$WILD_ANS" != "$WILDCARD_IP" ]; then
    log_fail "Wildcard synthesis failed! Expected $WILDCARD_IP, got '$WILD_ANS'"
fi
log_pass "Wildcard successfully synthesized answer for unallocated subdomain."

step "Test 2: Query specific override record (api.$ZONE must return $SPECIFIC_IP)"
SPECIFIC_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "api.${ZONE}." A +short)
echo "  Query: api.${ZONE}. -> Answer: $SPECIFIC_ANS"

if [ "$SPECIFIC_ANS" != "$SPECIFIC_IP" ]; then
    log_fail "Specific override failed! Expected $SPECIFIC_IP, got '$SPECIFIC_ANS'"
fi
log_pass "Specific record cleanly overrides wildcard."

step "Test 3: Verify DNSSEC cryptographic validity on wildcard zone"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC cryptographic validation failed on wildcard zone!"
fi
log_pass "DNSSEC signatures on wildcard zone verified cryptographically."

step "Cleanup"
delete_zone_all_views "$ZONE"

log_pass "Test records-wildcard-dnssec completed successfully."
