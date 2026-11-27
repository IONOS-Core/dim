#!/bin/bash
#
# suites/test-zone-profile-record-deletes.sh
# E2E Test: Deletion of profile-inherited records, template profile immutability, and PowerDNS convergence
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_pdel() {
    delete_zone_all_views prof-del-zone.com
    ndcli -q delete zone-profile prof-del-eval 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_pdel

step "Create template profile with NS, MX, and TXT records"
ndcli --quiet create zone-profile prof-del-eval primary "ns1.template.example.org." mail "hostmaster@template.example.org"
ndcli --quiet modify zone-profile prof-del-eval create rr @ NS "ns1.template.example.org."
ndcli --quiet modify zone-profile prof-del-eval create rr @ MX 10 mail.template.example.org.
ndcli --quiet modify zone-profile prof-del-eval create rr @ TXT default-verification-payload

step "Instantiate zone from profile and ensure output sync"
ndcli --quiet create zone prof-del-zone.com profile prof-del-eval
ensure_output_sync prof-del-zone.com

# Verify all 3 template records inherited in DIM
INIT_MX=$(ndcli list rrs "prof-del-zone.com." MX -H | awk '{print $6}')
assert_equals "Zone inherited template MX" mail.template.example.org. "$INIT_MX" "Mail Server"

INIT_TXT=$(ndcli list rrs "prof-del-zone.com." TXT -H | cut -f6-)
assert_contains "Zone inherited template TXT" default-verification-payload "$INIT_TXT"

step "Wait for PowerDNS to receive initial zone with inherited records"
SYNCED=false
PDNS_MX=""
for _ in $(seq 1 20); do
    PDNS_MX=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "prof-del-zone.com." MX +short)
    if echo "$PDNS_MX" | grep -q mail.template.example.org.; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS serves inherited template MX" "true" "$SYNCED"

step "Test 1: Delete inherited MX record from zone"
ndcli --quiet delete rr "prof-del-zone.com." MX 10 mail.template.example.org.

# Verify MX is absent in DIM
REMAINING_MX=$(ndcli list rrs "prof-del-zone.com." MX -H | awk '{print $6}')
assert_equals "MX record deleted from zone" "" "$REMAINING_MX" "Mail Server"

step "Test 2: Assert template profile immutability (profile still retains MX record)"
PROFILE_MX=$(ndcli list zone-profile prof-del-eval -H | awk '$2 == "MX" {print $4}')
assert_equals "Template profile MX remained untouched" mail.template.example.org. "$PROFILE_MX" "Profile Mail Server"

step "Test 3: Verify PowerDNS stops answering MX while remaining records stay active"
sleep 2

PDNS_MX_AFTER=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "prof-del-zone.com." MX +short)
assert_equals "PowerDNS no longer serves deleted MX" "" "$PDNS_MX_AFTER" "MX Answer"

PDNS_TXT_STILL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "prof-del-zone.com." TXT +short)
assert_contains "PowerDNS still serves unaffected TXT record" default-verification-payload "$PDNS_TXT_STILL"

step "Cleanup"
cleanup_pdel

log_pass "Profile-inherited record deletion and profile immutability test passed."
