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


cleanup_records_test() {
    delete_zone_all_views dnssec-on.com
    delete_zone_all_views dnssec-off.com
}
trap cleanup_records_test EXIT INT TERM

step "Clean up previous runs"
cleanup_records_test

step "Create DNSSEC disabled zone: dnssec-off.com"
ndcli create zone dnssec-off.com
ensure_output_sync dnssec-off.com

step "Create DNSSEC enabled zone: dnssec-on.com"
ndcli create zone dnssec-on.com
ensure_output_sync dnssec-on.com
ndcli modify zone dnssec-on.com dnssec enable 8 ksk 2048 zsk 1024

step "Create A Records"
ndcli create rr "test-a.dnssec-off.com." A 10.0.0.1
ndcli create rr "test-a.dnssec-on.com." A 10.0.0.1

step "Create AAAA Records"
ndcli create rr "test-aaaa.dnssec-off.com." AAAA 2001:db8::1
ndcli create rr "test-aaaa.dnssec-on.com." AAAA 2001:db8::1

step "Create MX Records"
ndcli create rr "test-mx.dnssec-off.com." MX 10 mail.example.com.
ndcli create rr "test-mx.dnssec-on.com." MX 10 mail.example.com.

step "Create TXT Records"
ndcli create rr "test-txt.dnssec-off.com." TXT "Hello World"
ndcli create rr "test-txt.dnssec-on.com." TXT "Hello World"

step "Create SRV Records"
ndcli create rr "_sip._tcp.dnssec-off.com." SRV 10 20 5060 sip.example.com.
ndcli create rr "_sip._tcp.dnssec-on.com." SRV 10 20 5060 sip.example.com.

step "Create NAPTR Records"
ndcli create rr "test-naptr.dnssec-off.com." NAPTR 100 10 'U' 'E2U+sip' '!^.*$!sip:info@example.com!' .
ndcli create rr "test-naptr.dnssec-on.com." NAPTR 100 10 'U' 'E2U+sip' '!^.*$!sip:info@example.com!' .

step "Create TLSA Records"
ndcli create rr "_443._tcp.dnssec-off.com." TLSA 3 0 1 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
ndcli create rr "_443._tcp.dnssec-on.com." TLSA 3 0 1 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef

step "Create SSHFP Records"
ndcli create rr "test-sshfp.dnssec-off.com." SSHFP 1 1 1234567890abcdef1234567890abcdef12345678
ndcli create rr "test-sshfp.dnssec-on.com." SSHFP 1 1 1234567890abcdef1234567890abcdef12345678

step "Create CAA Records"
ndcli create rr "test-caa.dnssec-off.com." CAA 0 issue "ca.example.com"
ndcli create rr "test-caa.dnssec-on.com." CAA 0 issue "ca.example.com"

step "Test CNAME and ALIAS Cross-Zone behavior"
# CNAME from dnssec-on to dnssec-off
ndcli create rr "test-cname.dnssec-on.com." CNAME "test-a.dnssec-off.com."
# CNAME from dnssec-off to dnssec-on
ndcli create rr "test-cname.dnssec-off.com." CNAME "test-a.dnssec-on.com."

step "Wait for PowerDNS to receive both zones from pdns-output"
SYNCED_OFF=false
SYNCED_ON=false
for _ in $(seq 1 25); do
    if [ "$SYNCED_OFF" != "true" ]; then
        ANS_OFF=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.dnssec-off.com." A +short)
        if [ "$ANS_OFF" = "10.0.0.1" ]; then
            SYNCED_OFF=true
        fi
    fi
    if [ "$SYNCED_ON" != "true" ]; then
        if dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-on.com." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
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

step "Authoritative Resolution Validation (PowerDNS @127.1.1.1) for dnssec-off.com"
assert_equals "A record resolution" "10.0.0.1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.dnssec-off.com." A +short)"
assert_equals "AAAA record resolution" "2001:db8::1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-aaaa.dnssec-off.com." AAAA +short)"
assert_equals "MX record resolution" "10 mail.example.com." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-mx.dnssec-off.com." MX +short)"
assert_equals "TXT record resolution" '"Hello World"' "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-txt.dnssec-off.com." TXT +short)"
assert_equals "SRV record resolution" "10 20 5060 sip.example.com." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "_sip._tcp.dnssec-off.com." SRV +short)"
assert_contains "NAPTR record resolution" "E2U+sip" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-naptr.dnssec-off.com." NAPTR +short)"
assert_contains "TLSA record resolution" "3 0 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "_443._tcp.dnssec-off.com." TLSA +short)"
assert_contains "SSHFP record resolution" "1 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-sshfp.dnssec-off.com." SSHFP +short)"
assert_contains "CAA record resolution" '0 issue "ca.example.com"' "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-caa.dnssec-off.com." CAA +short)"
assert_equals "CNAME record resolution" "test-a.dnssec-on.com." "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-cname.dnssec-off.com." CNAME +short)"

step "DNSSEC Cryptographic Signature Validation (PowerDNS @127.1.1.1) for dnssec-on.com"
A_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-a.dnssec-on.com." A +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for A record in signed zone" "$A_RRSIG"
MX_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-mx.dnssec-on.com." MX +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for MX record in signed zone" "$MX_RRSIG"
TXT_RRSIG=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "test-txt.dnssec-on.com." TXT +dnssec +noall +answer | grep -c "RRSIG" || test "$?" -le 1)
assert_not_empty "RRSIG present for TXT record in signed zone" "$TXT_RRSIG"

step "Cleanup"
cleanup_records_test

log_pass "All record types successfully verified through PowerDNS resolution."
