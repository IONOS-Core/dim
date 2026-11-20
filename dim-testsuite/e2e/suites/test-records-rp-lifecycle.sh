#!/bin/bash
#
# suites/test-records-rp-lifecycle.sh
# E2E Test: RFC 1183 Responsible Person (RP) record syntax, email conversion, TXT linking, and PowerDNS delivery
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="rp-lifecycle-test.com"
HOST="service.${ZONE}"
INFO_TXT="sysadmin-contact.${ZONE}"

cleanup_rp() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_rp

step "Create zone and add contact TXT record"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet create rr "${INFO_TXT}." TXT "Sysadmin On-Call: +49-721-12345"

step "Test 1: Create RP record linking email to TXT contact info"
ndcli --quiet create rr "$HOST." RP "hostmaster@${ZONE}" "${INFO_TXT}."

RP_VALUE=$(ndcli list rrs "$HOST." RP -H | awk '{print $5, $6}')
echo "  RP value in DIM: $RP_VALUE"

assert_contains "RP mbox converted to dotted format" "hostmaster.${ZONE}." "$RP_VALUE"
assert_contains "RP links to TXT contact record" "${INFO_TXT}." "$RP_VALUE"

step "Test 2: Create RP record with root '.' (no TXT record pointer)"
ndcli --quiet create rr "backup.${ZONE}." RP "noc@${ZONE}" .

RP_ROOT_VAL=$(ndcli list rrs "backup.${ZONE}." RP -H | awk '{print $5, $6}')
assert_contains "RP with root txtdname" "noc.${ZONE}. ." "$RP_ROOT_VAL"

step "Test 3: Invalid email syntax guard - Malformed mailbox rejected"
set +e
MALFORMED_OUT=$(ndcli create rr "invalid.${ZONE}." RP "not-an-email" . 2>&1)
EXIT_MALFORMED=$?
set -e

assert_exit_code "Malformed RP email rejected" 1 "$EXIT_MALFORMED" "$MALFORMED_OUT"
assert_contains "Invalid email error message" "Invalid email address" "$MALFORMED_OUT"

step "Wait for PowerDNS to receive and serve RP records"
SYNCED=false
PDNS_RP=""
for _ in $(seq 1 20); do
    PDNS_RP=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." RP +short)
    if echo "$PDNS_RP" | grep -q "hostmaster.${ZONE}."; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS serves authoritative RP record" "true" "$SYNCED"

step "Verify PowerDNS answer contains both mbox and txtdname"
PDNS_FULL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." RP +noall +answer)
assert_contains "PowerDNS answer contains converted mbox" "hostmaster.${ZONE}." "$PDNS_FULL"
assert_contains "PowerDNS answer contains txtdname target" "${INFO_TXT}." "$PDNS_FULL"

step "Cleanup"
cleanup_rp

log_pass "RFC 1183 RP record lifecycle test passed."
