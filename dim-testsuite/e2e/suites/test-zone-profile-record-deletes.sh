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

PROFILE="prof-del-eval"
ZONE="prof-del-zone.com"
TMPL_MX="mail.template.example.org."
TMPL_TXT="default-verification-payload"

cleanup_pdel() {
    delete_zone_all_views "$ZONE"
    ndcli -q delete zone-profile "$PROFILE" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_pdel

step "Create template profile with NS, MX, and TXT records"
ndcli --quiet create zone-profile "$PROFILE" primary "ns1.template.example.org." mail "hostmaster@template.example.org"
ndcli --quiet modify zone-profile "$PROFILE" create rr @ NS "ns1.template.example.org."
ndcli --quiet modify zone-profile "$PROFILE" create rr @ MX 10 "$TMPL_MX"
ndcli --quiet modify zone-profile "$PROFILE" create rr @ TXT "$TMPL_TXT"

step "Instantiate zone from profile and ensure output sync"
ndcli --quiet create zone "$ZONE" profile "$PROFILE"
ensure_output_sync "$ZONE"

# Verify all 3 template records inherited in DIM
INIT_MX=$(ndcli list rrs "${ZONE}." MX -H | awk '{print $6}')
assert_equals "Zone inherited template MX" "$TMPL_MX" "$INIT_MX" "Mail Server"

INIT_TXT=$(ndcli list rrs "${ZONE}." TXT -H | cut -f6-)
assert_contains "Zone inherited template TXT" "$TMPL_TXT" "$INIT_TXT"

step "Wait for PowerDNS to receive initial zone with inherited records"
SYNCED=false
PDNS_MX=""
for _ in $(seq 1 20); do
    PDNS_MX=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." MX +short)
    if echo "$PDNS_MX" | grep -q "$TMPL_MX"; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS serves inherited template MX" "true" "$SYNCED"

step "Test 1: Delete inherited MX record from zone"
ndcli --quiet delete rr "${ZONE}." MX 10 "$TMPL_MX"

# Verify MX is absent in DIM
REMAINING_MX=$(ndcli list rrs "${ZONE}." MX -H | awk '{print $6}')
assert_equals "MX record deleted from zone" "" "$REMAINING_MX" "Mail Server"

step "Test 2: Assert template profile immutability (profile still retains MX record)"
PROFILE_MX=$(ndcli list zone-profile "$PROFILE" -H | awk '$2 == "MX" {print $4}')
assert_equals "Template profile MX remained untouched" "$TMPL_MX" "$PROFILE_MX" "Profile Mail Server"

step "Test 3: Verify PowerDNS stops answering MX while remaining records stay active"
sleep 2

PDNS_MX_AFTER=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." MX +short)
assert_equals "PowerDNS no longer serves deleted MX" "" "$PDNS_MX_AFTER" "MX Answer"

PDNS_TXT_STILL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." TXT +short)
assert_contains "PowerDNS still serves unaffected TXT record" "$TMPL_TXT" "$PDNS_TXT_STILL"

step "Cleanup"
cleanup_pdel

log_pass "Profile-inherited record deletion and profile immutability test passed."
