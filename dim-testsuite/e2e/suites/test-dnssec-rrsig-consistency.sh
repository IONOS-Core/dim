#!/bin/bash
#
# suites/test-dnssec-rrsig-consistency.sh
# E2E Test: Multi-record RRSIG timestamp synchronicity and zero intra-zone inception jitter
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="rrsig-sync-eval.com"

cleanup_sync() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_sync

step "Create DNSSEC zone with diverse record types (SOA, DNSKEY, A, MX, TXT, NSEC)"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
ndcli --quiet create rr "web.${ZONE}." A 10.10.1.1
ndcli --quiet create rr "mail.${ZONE}." MX 10 "mailhost.${ZONE}."
ndcli --quiet create rr "${ZONE}." TXT "timestamp-consistency-probe"

step "Wait for PowerDNS to receive all signed records"
SYNCED=false
PDNS_ANS=""
for _ in $(seq 1 20); do
    PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "web.${ZONE}." A +short)
    if [ "$PDNS_ANS" = "10.10.1.1" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received signed records" "true" "$SYNCED"

# Get domain_id from PowerDNS database
DOMAIN_ID=$(pdns_sql "SELECT id FROM domains WHERE name='${ZONE}';")
assert_not_empty "PowerDNS domain entry" "$DOMAIN_ID" "Domain ID"

step "Test 1: Assert zero intra-zone inception jitter across all RRSIG records"
# Inception timestamp is the 6th field in RRSIG content (YYYYMMDDHHmmss)
DISTINCT_INCEPTIONS=$(pdns_sql "
    SELECT count(DISTINCT substring_index(substring_index(content, ' ', 6), ' ', -1))
    FROM records
    WHERE domain_id=${DOMAIN_ID} AND type='RRSIG';
")
echo "  Distinct RRSIG inceptions in zone: $DISTINCT_INCEPTIONS"

assert_equals "All RRSIGs share identical inception timestamp (0s jitter)" "1" "$DISTINCT_INCEPTIONS" "Distinct Count"

step "Test 2: Assert zero intra-zone expiration jitter across all RRSIG records"
# Expiration timestamp is the 5th field in RRSIG content (YYYYMMDDHHmmss)
DISTINCT_EXPIRATIONS=$(pdns_sql "
    SELECT count(DISTINCT substring_index(substring_index(content, ' ', 5), ' ', -1))
    FROM records
    WHERE domain_id=${DOMAIN_ID} AND type='RRSIG';
")
echo "  Distinct RRSIG expirations in zone: $DISTINCT_EXPIRATIONS"

assert_equals "All RRSIGs share identical expiration timestamp (0s jitter)" "1" "$DISTINCT_EXPIRATIONS" "Distinct Count"

step "Test 3: Assert DNSKEY RRSIG is synchronized with SOA and RRset RRSIGs"
DNSKEY_INC=$(pdns_sql "
    SELECT substring_index(substring_index(content, ' ', 6), ' ', -1)
    FROM records
    WHERE domain_id=${DOMAIN_ID} AND type='RRSIG' AND substring_index(content, ' ', 1)='DNSKEY'
    LIMIT 1;
")

SOA_INC=$(pdns_sql "
    SELECT substring_index(substring_index(content, ' ', 6), ' ', -1)
    FROM records
    WHERE domain_id=${DOMAIN_ID} AND type='RRSIG' AND substring_index(content, ' ', 1)='SOA'
    LIMIT 1;
")

assert_equals "DNSKEY inception matches SOA inception" "$SOA_INC" "$DNSKEY_INC" "Timestamp"

step "Test 4: Verify Cryptographic and Inception Plausibility via verify-dnssec-zone.py"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed for $ZONE!"
fi
log_pass "Zone signatures verified cryptographically with valid non-future inceptions."

step "Cleanup"
cleanup_sync

log_pass "RRSIG intra-zone timestamp consistency test passed."
