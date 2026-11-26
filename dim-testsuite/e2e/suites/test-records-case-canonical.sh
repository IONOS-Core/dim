#!/bin/bash
#
# suites/test-records-case-canonical.sh
# E2E Test: RFC 4343 DNS case-insensitivity, duplicate case normalization, and RFC 4034 DNSSEC canonicalization
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_case() {
    delete_zone_all_views case-canonical-test.com
}

step "Clean up previous runs"
cleanup_case

step "Create DNSSEC-enabled zone"
ndcli --quiet create zone case-canonical-test.com
ensure_output_sync case-canonical-test.com
ndcli --quiet modify zone case-canonical-test.com dnssec enable 8 ksk 2048 zsk 1024

step "Test 1: Create lowercase record"
ndcli --quiet create rr myhost.case-canonical-test.com. A 10.1.1.1 --only-forward

step "Test 2: Case Normalization - Creating uppercase record is detected as duplicate"
set +e
DUP_OUT=$(ndcli create rr MYHOST.case-canonical-test.com. A 10.1.1.1 --only-forward 2>&1)
DUP_EXIT=$?
set -e

assert_exit_code "Uppercase duplicate creation exit code" 0 "$DUP_EXIT" "$DUP_OUT"
assert_contains "Duplicate detection message" "already exists" "$DUP_OUT"

# Verify only 1 record exists in database
RECORD_COUNT=$(ndcli list rrs "*.case-canonical-test.com." A -H | grep -c "myhost" || true)
assert_equals "Total records in zone after case-insensitive duplicate attempt" "1" "$RECORD_COUNT" "Count"

step "Test 3: Case-insensitive query matching"
QUERY_OUT=$(ndcli list rrs MyHost.case-canonical-test.com. A -H | awk '{print $5}')
assert_equals "Mixed-case query resolves to canonical record IP" "10.1.1.1" "$QUERY_OUT" "IP"

step "Wait for PowerDNS to receive signed record"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" MyHost.case-canonical-test.com. A +short || true)
    if [ "$PDNS_ANS" = "10.1.1.1" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS answers case-insensitive query" "true" "$SYNCED"

step "Test 4: Verify RFC 4034 Canonical DNSSEC signing"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" case-canonical-test.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC cryptographic verification failed on canonicalized zone!"
fi
log_pass "Zone signatures verified cryptographically with canonical lowercase names."

step "Cleanup"
cleanup_case

log_pass "RFC 4343 case-insensitivity and RFC 4034 DNSSEC canonicalization test passed."
