#!/bin/bash
#
# suites/test-dnssec-output-removal.sh
# E2E Test: Complete teardown and database metadata purge when a DNSSEC-signed zone is detached from output
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_dnssec_rem() {
    delete_zone_all_views dnssec-purge-eval.com
    ndcli -q modify output out remove zone-group purge-test-zg 2>/dev/null || true
    ndcli -q delete zone-group purge-test-zg 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_dnssec_rem

step "Create dedicated zone-group, attach to output, and create DNSSEC NSEC3 zone"
ndcli --quiet create zone-group purge-test-zg
ndcli --quiet modify output out add zone-group purge-test-zg

ndcli --quiet create zone dnssec-purge-eval.com
ndcli --quiet modify zone-group purge-test-zg add zone dnssec-purge-eval.com
ndcli --quiet modify zone dnssec-purge-eval.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 10 salted
ndcli --quiet create rr "host1.dnssec-purge-eval.com." A 10.1.2.3

step "Wait for PowerDNS to receive signed zone and metadata"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "host1.dnssec-purge-eval.com." A +short || true)
    if [ "$PDNS_ANS" = "10.1.2.3" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received signed zone" "true" "$SYNCED"

# Capture domain ID from PowerDNS MySQL
DOMAIN_ID=$(pdns_sql "SELECT id FROM domains WHERE name='dnssec-purge-eval.com';")
echo "  PowerDNS domain_id: $DOMAIN_ID"
assert_not_empty "PowerDNS domain entry created" "$DOMAIN_ID" "Domain ID"

# Verify initial records and metadata exist in PowerDNS backend
INIT_RECORDS=$(pdns_sql "SELECT count(*) FROM records WHERE domain_id=${DOMAIN_ID};")
INIT_META=$(pdns_sql "SELECT count(*) FROM domainmetadata WHERE domain_id=${DOMAIN_ID};")

echo "  Initial state in PowerDNS: records=$INIT_RECORDS, metadata=$INIT_META"
if [ "$INIT_RECORDS" -lt 5 ] || [ "$INIT_META" -lt 3 ]; then
    log_fail "PowerDNS missing expected DNSSEC records or metadata entries."
fi
log_pass "PowerDNS holds records, DNSKEYs, RRSIGs, and NSEC3PARAM metadata."

step "Test 1: Detach zone from zone-group (trigger authoritative purge)"
ndcli --quiet modify zone-group purge-test-zg remove zone dnssec-purge-eval.com

# Wait for pdns-output to process delete_zone
sleep 3

step "Test 2: Assert complete purge of domain, records, and metadata in PowerDNS database"
DOMAIN_AFTER=$(pdns_sql "SELECT count(*) FROM domains WHERE name='dnssec-purge-eval.com';")
assert_equals "Domain entry purged from PowerDNS domains table" "0" "$DOMAIN_AFTER" "Row Count"

RECORDS_AFTER=$(pdns_sql "SELECT count(*) FROM records WHERE domain_id=${DOMAIN_ID};")
assert_equals "All records (RRs, DNSKEY, RRSIG) purged from PowerDNS records table" "0" "$RECORDS_AFTER" "Row Count"

META_AFTER=$(pdns_sql "SELECT count(*) FROM domainmetadata WHERE domain_id=${DOMAIN_ID};")
assert_equals "All DNSSEC metadata (NSEC3PARAM, SIGN_PARAM, PRESIGNED) purged" "0" "$META_AFTER" "Row Count"

step "Test 3: Assert PowerDNS no longer answers queries for detached zone"
PDNS_QUERY_AFTER=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "host1.dnssec-purge-eval.com." A +short || true)
assert_equals "PowerDNS answer is empty for purged zone" "" "$PDNS_QUERY_AFTER" "Query Answer"

step "Cleanup"
cleanup_dnssec_rem

log_pass "DNSSEC zone output removal and metadata purge test passed."
