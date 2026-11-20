#!/bin/bash
#
# suites/test-dnssec-gatekeeper-near-expiry.sh
# End-to-end test proving the need for a minimum expiration safety margin in DnssecGatekeeper.
#
# Threat Model (The Near-Expiry Outage Trap):
# DnssecGatekeeper currently only checks 'if (end <= now)'.
# If valid_end is set to expire in 60 seconds (due to clock skew, batch delay, or renewal stall),
# DnssecGatekeeper allows the transaction to PASS and publishes the signatures to PowerDNS.
#
# Resolvers cache the DNS records with the zone's standard TTL (e.g. 86400s / 1 day),
# but the RRSIG signature expires after only 60 seconds.
# Resolvers then encounter an expired signature while the RRset is cached, resulting in
# worldwide SERVFAIL validation outages!
#
# Expected Behavior (RFC 6781 Compliance):
# DnssecGatekeeper MUST enforce a minimum validity margin (e.g. valid_end - now >= 3600s).
# If signatures would expire almost immediately, DnssecGatekeeper must throw
# SignatureRefusedException and abort, preserving healthy existing records in PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="near-expiry-eval.com"

cleanup_near_expiry() {
    delete_zone_all_views "$ZONE"
}
trap cleanup_near_expiry EXIT INT TERM

step "Clean up previous runs"
cleanup_near_expiry

step "Create zone and enable DNSSEC"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli --quiet modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
ndcli --quiet create rr "service.${ZONE}." A 10.99.1.1

# Wait for initial healthy signature
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "Initial DNSSEC zone active in PowerDNS" "true" "$SYNCED"

step "Test 1: Simulate near-expiry validity window (valid_end = now + 60s)"
# Artificially set valid_end to expire in 60 seconds from now
sql_command "UPDATE zone SET valid_begin = DATE_SUB(UTC_TIMESTAMP(), INTERVAL 14 DAY), \
                            valid_end = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 60 SECOND) \
             WHERE name='${ZONE}';"

# Trigger an update to force re-signing under near-expiry window
ndcli --quiet create rr "trigger.${ZONE}." TXT "near-expiry-trigger"

step "Test 2: Check whether DnssecGatekeeper refused or allowed near-expiry signatures"
# Allow pdns-output time to evaluate
sleep 3

# Query PowerDNS for the newly updated record
TRIGGER_PUBLISHED=false
if [ "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "trigger.${ZONE}." TXT +short)" = '"near-expiry-trigger"' ]; then
    TRIGGER_PUBLISHED=true
fi

echo "  Near-expiry signature published to PowerDNS: $TRIGGER_PUBLISHED"

# RFC 6781 Safety Guard Assertion:
# DnssecGatekeeper SHOULD refuse signatures expiring in < 3600s to prevent resolver SERVFAIL!
if [ "$TRIGGER_PUBLISHED" = "true" ]; then
    # Query expiration timestamp from PowerDNS RRSIG
    RRSIG_LINE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "trigger.${ZONE}." TXT +dnssec +noall +answer | grep "RRSIG" | head -1)
    EXP_STR=$(echo "$RRSIG_LINE" | awk '{print $9}')
    echo "  Published RRSIG expiration: $EXP_STR (expires in < 60s!)"

    log_fail "DEFECT DEMONSTRATED: DnssecGatekeeper permitted publishing RRSIG expiring in 60s, creating worldwide resolver SERVFAIL outage!"
else
    log_pass "DnssecGatekeeper correctly refused near-expiry signatures."
fi

step "Cleanup"
cleanup_near_expiry
