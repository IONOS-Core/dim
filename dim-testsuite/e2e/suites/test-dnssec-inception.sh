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


step "Clean up previous runs"
delete_zone_all_views dnssec-inception-test.com

step "Create DNSSEC-signed zone: dnssec-inception-test.com"
ndcli create zone dnssec-inception-test.com
ensure_output_sync dnssec-inception-test.com
ndcli modify zone dnssec-inception-test.com dnssec enable 8 ksk 2048 zsk 1024

step "Add test resource records"
ndcli create rr "www.dnssec-inception-test.com." A 10.99.1.1
ndcli create rr "mail.dnssec-inception-test.com." MX 10 "mail.example.com."
ndcli create rr "dnssec-inception-test.com." TXT "dnssec inception validation probe"

step "Wait for pdns-output daemon to sign and synchronize zone to PowerDNS"
# Poll PowerDNS authoritative nameserver (127.1.1.1) for SOA + RRSIG
MAX_WAIT=20
WAIT_COUNT=0
SYNCED=false

while [ "$WAIT_COUNT" -lt "$MAX_WAIT" ]; do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-inception-test.com." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
    WAIT_COUNT=$((WAIT_COUNT + 1))
done

if [ "$SYNCED" = false ]; then
    log_fail "Timeout waiting for PowerDNS to receive signed zone dnssec-inception-test.com."
fi
log_pass "Zone synchronized and signed in PowerDNS."

step "Execute Cryptographic & RFC 6781 Inception Verification"
# Verify against local PowerDNS using verify-dnssec-zone.py
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" dnssec-inception-test.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC Inception/Validity verification failed for dnssec-inception-test.com!"
fi

log_pass "DNSSEC signature inception is valid (not in the future) and cryptographically sound."

step "Cleanup"
delete_zone_all_views dnssec-inception-test.com

log_pass "Test dnssec-inception completed successfully."
