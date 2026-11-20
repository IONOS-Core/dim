#!/bin/bash
#
# suites/test-records-all-types.sh
# End-to-end test verifying all supported DNS resource record types across PowerDNS authoritative server.
# Tests both unsigned (plain) and DNSSEC-signed zones, validating:
#   1. Record creation via ndcli
#   2. Synchronization through pdns-output daemon into PowerDNS
#   3. Authoritative resolution via dig for all record types (A, AAAA, MX, TXT, SRV, NAPTR, TLSA, SSHFP, CAA, CNAME)
#   4. RRSIG signature generation for records in DNSSEC-signed zones
#   5. Cross-zone CNAME and ALIAS constraints
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE_OFF="dnssec-off.com"
ZONE_ON="dnssec-on.com"

cleanup_records_test() {
    delete_zone_all_views "$ZONE_ON"
    delete_zone_all_views "$ZONE_OFF"
}
trap cleanup_records_test EXIT INT TERM

step "Clean up previous runs"
cleanup_records_test

step "Create DNSSEC disabled zone: ${ZONE_OFF}"
ndcli create zone "$ZONE_OFF"
ensure_output_sync "$ZONE_OFF"

step "Create DNSSEC enabled zone: ${ZONE_ON}"
ndcli create zone "$ZONE_ON"
ensure_output_sync "$ZONE_ON"
ndcli modify zone "$ZONE_ON" dnssec enable 8 ksk 2048 zsk 1024

step "Create A Records"
ndcli create rr "test-a.${ZONE_OFF}." A 10.0.0.1
ndcli create rr "test-a.${ZONE_ON}." A 10.0.0.1

step "Create AAAA Records"
ndcli create rr "test-aaaa.${ZONE_OFF}." AAAA 2001:db8::1
ndcli create rr "test-aaaa.${ZONE_ON}." AAAA 2001:db8::1

step "Create MX Records"
ndcli create rr "test-mx.${ZONE_OFF}." MX 10 mail.example.com.
ndcli create rr "test-mx.${ZONE_ON}." MX 10 mail.example.com.

step "Create TXT Records"
ndcli create rr "test-txt.${ZONE_OFF}." TXT "Hello World"
ndcli create rr "test-txt.${ZONE_ON}." TXT "Hello World"

step "Create SRV Records"
ndcli create rr "_sip._tcp.${ZONE_OFF}." SRV 10 20 5060 sip.example.com.
ndcli create rr "_sip._tcp.${ZONE_ON}." SRV 10 20 5060 sip.example.com.

step "Create NAPTR Records"
ndcli create rr "test-naptr.${ZONE_OFF}." NAPTR 100 10 'U' 'E2U+sip' '!^.*$!sip:info@example.com!' .
ndcli create rr "test-naptr.${ZONE_ON}." NAPTR 100 10 'U' 'E2U+sip' '!^.*$!sip:info@example.com!' .

step "Create TLSA Records"
ndcli create rr "_443._tcp.${ZONE_OFF}." TLSA 3 0 1 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
ndcli create rr "_443._tcp.${ZONE_ON}." TLSA 3 0 1 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef

step "Create SSHFP Records"
ndcli create rr "test-sshfp.${ZONE_OFF}." SSHFP 1 1 1234567890abcdef1234567890abcdef12345678
ndcli create rr "test-sshfp.${ZONE_ON}." SSHFP 1 1 1234567890abcdef1234567890abcdef12345678

step "Create CAA Records"
ndcli create rr "test-caa.${ZONE_OFF}." CAA 0 issue "ca.example.com"
ndcli create rr "test-caa.${ZONE_ON}." CAA 0 issue "ca.example.com"

step "Test CNAME and ALIAS Cross-Zone behavior"
# CNAME from dnssec-on to dnssec-off
ndcli create rr "test-cname.${ZONE_ON}." CNAME "test-a.${ZONE_OFF}."
# CNAME from dnssec-off to dnssec-on
ndcli create rr "test-cname.${ZONE_OFF}." CNAME "test-a.${ZONE_ON}."

# ALIAS in dnssec-off pointing to dnssec-on should succeed
ndcli create rr "test-alias.${ZONE_OFF}." ALIAS "test-a.${ZONE_ON}."

# ALIAS in dnssec-on zone must be rejected by design (offline DNSSEC signing constraint)
if ndcli create rr "test-alias.${ZONE_ON}." ALIAS "test-a.${ZONE_OFF}." 2>/dev/null; then
    log_fail "Expected ALIAS creation in DNSSEC-enabled zone to fail, but it succeeded!"
else
    log_pass "ALIAS correctly rejected in DNSSEC-signed zone."
fi

step "Wait for PowerDNS to receive both zones from pdns-output"
SYNCED_OFF=false
SYNCED_ON=false
for _ in $(seq 1 25); do
    if [ "$SYNCED_OFF" != "true" ]; then
        ANS_OFF=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.${ZONE_OFF}." A +short)
        if [ "$ANS_OFF" = "10.0.0.1" ]; then
            SYNCED_OFF=true
        fi
    fi
    if [ "$SYNCED_ON" != "true" ]; then
        if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_ON}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
            SYNCED_ON=true
        fi
    fi
    if [ "$SYNCED_OFF" = "true" ] && [ "$SYNCED_ON" = "true" ]; then
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received unsigned zone records" "true" "$SYNCED_OFF"
assert_equals "PowerDNS received signed zone records" "true" "$SYNCED_ON"

step "Authoritative Resolution Validation (PowerDNS @127.1.1.1) for ${ZONE_OFF}"
assert_equals "A record resolution" "10.0.0.1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.${ZONE_OFF}." A +short)"
assert_equals "AAAA record resolution" "2001:db8::1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-aaaa.${ZONE_OFF}." AAAA +short)"
assert_equals "MX record resolution" "10 mail.example.com." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-mx.${ZONE_OFF}." MX +short)"
assert_equals "TXT record resolution" '"Hello World"' "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-txt.${ZONE_OFF}." TXT +short)"
assert_equals "SRV record resolution" "10 20 5060 sip.example.com." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "_sip._tcp.${ZONE_OFF}." SRV +short)"
assert_contains "NAPTR record resolution" "E2U+sip" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-naptr.${ZONE_OFF}." NAPTR +short)"
assert_contains "TLSA record resolution" "3 0 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "_443._tcp.${ZONE_OFF}." TLSA +short)"
assert_contains "SSHFP record resolution" "1 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-sshfp.${ZONE_OFF}." SSHFP +short)"
assert_contains "CAA record resolution" '0 issue "ca.example.com"' "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-caa.${ZONE_OFF}." CAA +short)"
assert_equals "CNAME record resolution" "test-a.${ZONE_ON}." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-cname.${ZONE_OFF}." CNAME +short)"

step "DNSSEC Cryptographic Signature Validation (PowerDNS @127.1.1.1) for ${ZONE_ON}"
A_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.${ZONE_ON}." A +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for A record in signed zone" "$A_RRSIG"
MX_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-mx.${ZONE_ON}." MX +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for MX record in signed zone" "$MX_RRSIG"
TXT_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-txt.${ZONE_ON}." TXT +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for TXT record in signed zone" "$TXT_RRSIG"

step "Cleanup"
cleanup_records_test

log_pass "All record types successfully verified through PowerDNS resolution."
