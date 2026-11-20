#!/bin/bash
#
# suites/test-dnssec-inception.sh
# End-to-end test verifying DNSSEC signature inception and validity windows.
# Proves that RRSIG inception timestamps are never generated in the future
# (guards against timezone mismatch between database sessions and JVM/host),
# and verifies RFC 6781 backdating and RSA-SHA256 signature validity against PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="dnssec-inception-test.com"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"

step "Create DNSSEC-signed zone: $ZONE"
ndcli create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024

step "Add test resource records"
ndcli create rr "www.${ZONE}." A 10.99.1.1
ndcli create rr "mail.${ZONE}." MX 10 "mail.example.com."
ndcli create rr "${ZONE}." TXT "dnssec inception validation probe"

step "Wait for pdns-output daemon to sign and synchronize zone to PowerDNS"
# Poll PowerDNS authoritative nameserver (127.1.1.1) for SOA + RRSIG
MAX_WAIT=20
WAIT_COUNT=0
SYNCED=false

while [ "$WAIT_COUNT" -lt "$MAX_WAIT" ]; do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

if [ "$SYNCED" = false ]; then
    log_fail "Timeout waiting for PowerDNS to receive signed zone ${ZONE}."
fi
log_pass "Zone synchronized and signed in PowerDNS."

step "Execute Cryptographic & RFC 6781 Inception Verification"
# Verify against local PowerDNS using verify-dnssec-zone.py
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC Inception/Validity verification failed for $ZONE!"
fi

log_pass "DNSSEC signature inception is valid (not in the future) and cryptographically sound."

step "Cleanup"
delete_zone_all_views "$ZONE"

log_pass "Test dnssec-inception completed successfully."
