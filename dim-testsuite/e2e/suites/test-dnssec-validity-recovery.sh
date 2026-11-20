#!/bin/bash
#
# suites/test-dnssec-validity-recovery.sh
# E2E Test: Disaster recovery after zone signature expiration, timestamp re-anchoring, and PowerDNS re-signing
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="validity-recovery-eval.com"

cleanup_rec() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_rec

step "Create DNSSEC zone and verify initial valid state"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
ndcli --quiet create rr "service.${ZONE}." A 10.99.1.1

# Wait for initial sync in PowerDNS
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "Initial zone active in PowerDNS" "true" "$SYNCED"

step "Simulate disaster: Artificially expire zone in database (valid_end 24 hours in past)"
sql_command "UPDATE zone SET valid_begin = DATE_SUB(NOW(), INTERVAL 15 DAY), valid_end = DATE_SUB(NOW(), INTERVAL 24 HOUR) WHERE name='${ZONE}';"

EXPIRED_END=$(sql_command "SELECT valid_end < NOW() FROM zone WHERE name='${ZONE}';")
assert_equals "Zone validity window is expired in database" "1" "$EXPIRED_END" "Boolean Flag"

step "Trigger automated validity renewal: manage_dim update-validity"
RENEW_OUTPUT=$(/src/dim-testsuite/virtual_env/bin/manage_dim update-validity --verbose 2>&1)
echo "$RENEW_OUTPUT"

assert_contains "Renewal script queued zone for signing" "queued in outputupdate" "$RENEW_OUTPUT"

step "Wait for pdns-output daemon to re-sign and publish refreshed RRSIGs to PowerDNS"
sleep 4

# Check new valid_end in DIM database is in the future
FUTURE_END=$(sql_command "SELECT valid_end > NOW() FROM zone WHERE name='${ZONE}';")
assert_equals "Zone validity end re-anchored to future" "1" "$FUTURE_END" "Boolean Flag"

step "Assert PowerDNS serves freshly generated non-expired RRSIGs"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed after disaster recovery!"
fi
log_pass "Zone recovered from expiration and validated cryptographically."

step "Cleanup"
cleanup_rec

log_pass "Disaster recovery validity renewal test passed."
