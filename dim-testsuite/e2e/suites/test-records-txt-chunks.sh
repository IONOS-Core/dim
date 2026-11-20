#!/bin/bash
#
# suites/test-records-txt-chunks.sh
# E2E Test: RFC 1035 255-byte TXT record chunking, multi-string DKIM payloads, and DNSSEC signing
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="txt-chunk-eval.com"

cleanup_txt() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_txt

step "Create DNSSEC-enabled zone"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024

step "Test 1: Standard short TXT record (< 255 characters)"
SHORT_TXT="v=spf1 include:_spf.google.com ~all"
ndcli --quiet create rr "spf.${ZONE}." TXT "$SHORT_TXT"

step "Test 2: Long 414-character DKIM key auto-split into RFC 1035 255-byte chunks"
DKIM_KEY="v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"

OUTPUT_DKIM=$(ndcli create rr "dkim._domainkey.${ZONE}." TXT "$DKIM_KEY" 2>&1)
echo "$OUTPUT_DKIM"

# Assert automatic division warning
assert_contains "RFC 1035 chunking warning emitted" "longer than 255 characters, it was automatically divided" "$OUTPUT_DKIM"

# Check chunking in DIM: must have two quoted strings
DIM_TXT_VALUE=$(ndcli list rrs "dkim._domainkey.${ZONE}." TXT -H | cut -f6-)
assert_contains "DIM stored first 255-byte chunk" "v=DKIM1;" "$DIM_TXT_VALUE"
assert_contains "DIM stored second remaining chunk" "123456789abcdef" "$DIM_TXT_VALUE"

step "Wait for PowerDNS to receive signed TXT records"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_DKIM=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "dkim._domainkey.${ZONE}." TXT +short || true)
    if echo "$PDNS_DKIM" | grep -q "v=DKIM1;"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received chunked DKIM record" "true" "$SYNCED"

step "Test 3: PowerDNS authoritative answer preserves multi-chunk format"
PDNS_FULL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "dkim._domainkey.${ZONE}." TXT +dnssec +noall +answer)

# Assert both chunks are present in the answer
assert_contains "PowerDNS answer contains opening chunk" "v=DKIM1;" "$PDNS_FULL"
assert_contains "PowerDNS answer contains RRSIG covering TXT" "RRSIG" "$PDNS_FULL"

step "Test 4: Verify Cryptographic Integrity of zone with chunked TXT"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC cryptographic verification failed on zone with chunked TXT records!"
fi
log_pass "Zone with RFC 1035 chunked TXT records verified cryptographically."

step "Cleanup"
cleanup_txt

log_pass "RFC 1035 TXT chunking and DNSSEC signing test passed."
