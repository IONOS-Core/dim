#!/bin/bash
#
# suites/test-dnssec-insecure-delegation.sh
# E2E Test: RFC 5155 NSEC3 denial of existence for insecure delegations vs signed delegations
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="deleg-proof-test.com"
INSECURE_SUB="insec.${ZONE}"
SECURE_SUB="sec.${ZONE}"

cleanup_deleg_proof() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_deleg_proof

step "Create DNSSEC zone with NSEC3 enabled (flags=0, opt-out disabled)"
ndcli create zone "$ZONE" --quiet
ensure_output_sync "$ZONE"
ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024 nsec3 10 salted --quiet

# Add an insecure delegation (NS only, no DS)
ndcli modify zone "$ZONE" create rr insec NS "ns1.insecure-partner.example.org." --quiet

# Add a secure delegation (NS + DS record)
ndcli modify zone "$ZONE" create rr sec NS "ns1.secure-partner.example.org." --quiet
ndcli modify zone "$ZONE" create rr sec DS 12345 8 2 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef --quiet

step "Wait for PowerDNS to receive signed zone with delegations"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${SECURE_SUB}." DS +noall +answer | grep -q "IN.*DS"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received secure delegation" "true" "$SYNCED"

step "Test 1: Verify NSEC3PARAM flags=0 (opt-out disabled for full security coverage)"
PARAM_FLAGS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." NSEC3PARAM +short | awk '{print $2}')
assert_equals "NSEC3 flags field is 0 (opt-out disabled)" "0" "$PARAM_FLAGS" "Flag"

step "Test 2: Insecure Delegation - Querying DS returns NOERROR + NSEC3 proof without DS in bitmap"
INSEC_RESP=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${INSECURE_SUB}." DS +dnssec)

# Must return NOERROR status
assert_contains "Insecure delegation status is NOERROR" "status: NOERROR" "$INSEC_RESP"

# Authority section must contain NSEC3 record
assert_contains "Authority section contains NSEC3 proof" "NSEC3 1 0 10" "$INSEC_RESP"

# NSEC3 record bitmap must list NS
assert_contains "NSEC3 bitmap covers NS" " NS" "$INSEC_RESP"

# Answer section must be empty (ANSWER: 0)
assert_contains "Answer count is 0 for insecure DS" "ANSWER: 0" "$INSEC_RESP"

step "Test 3: Secure Delegation - Querying DS returns DS record in Answer section with RRSIG"
SEC_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${SECURE_SUB}." DS +dnssec +noall +answer)
assert_contains "Secure delegation returns DS record" "12345 8 2" "$SEC_ANS"
assert_contains "Secure delegation DS covered by RRSIG" "RRSIG" "$SEC_ANS"

step "Verify Cryptographic Integrity of parent zone"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed for parent zone $ZONE!"
fi
log_pass "Parent zone with delegations verified cryptographically."

step "Cleanup"
cleanup_deleg_proof

log_pass "DNSSEC insecure delegation proof test passed."
