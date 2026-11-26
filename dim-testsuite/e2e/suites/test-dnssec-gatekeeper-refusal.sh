#!/bin/bash
#
# suites/test-dnssec-gatekeeper-refusal.sh
# End-to-end test verifying the DnssecGatekeeper pre-publish safety barrier in pdns-output.
#
# Protection Mechanism Verification:
# When database or system clock skew causes future-dated signature inceptions (valid_begin > now),
# DnssecGatekeeper MUST intercept the transaction before any writes to PowerDNS take place,
# throw SignatureRefusedException, log [DNSSEC] [REFUSED], and roll back the database transaction.
#
# Proves that:
#   1. Healthy signed zones are synchronized and validated in PowerDNS.
#   2. Corrupt/future-dated validity timestamps injected into DIM are REFUSED by pdns-output.
#   3. PowerDNS is protected: neither SOA serial nor RRSIG timestamps are corrupted.
#   4. Restoring valid timestamps enables pdns-output to recover and synchronize cleanly.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_gatekeeper_test() {
    # Ensure valid timestamps in DB before deletion so pdns-output can drain queue cleanly
    mysql -h127.0.0.1 -P3307 -udim -pdim dim -e "UPDATE zone SET valid_begin = DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE name = 'dnssec-gatekeeper-guard.com';" 2>/dev/null || true
    sleep 2
    delete_zone_all_views dnssec-gatekeeper-guard.com
}
trap cleanup_gatekeeper_test EXIT INT TERM

step "Clean up previous runs"
delete_zone_all_views dnssec-gatekeeper-guard.com

step "Create baseline DNSSEC-signed zone: dnssec-gatekeeper-guard.com"
ndcli create zone dnssec-gatekeeper-guard.com
ensure_output_sync dnssec-gatekeeper-guard.com
ndcli modify zone dnssec-gatekeeper-guard.com dnssec enable 8 ksk 2048 zsk 1024
ndcli create rr "www.dnssec-gatekeeper-guard.com." A 10.88.1.1

step "Wait for initial signing in PowerDNS"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-gatekeeper-guard.com." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "Initial zone signed and active in PowerDNS" "true" "$SYNCED"

step "Capture PowerDNS baseline state"
SOA_BASELINE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-gatekeeper-guard.com." SOA +dnssec +noall +answer)
BASE_SERIAL=$(echo "$SOA_BASELINE" | awk '$4 == "SOA" {print $7}')
BASE_EXP=$(echo "$SOA_BASELINE" | awk '$4 == "RRSIG" {print $9}')
BASE_INC=$(echo "$SOA_BASELINE" | awk '$4 == "RRSIG" {print $10}')

echo "  Baseline SOA Serial: $BASE_SERIAL"
echo "  Baseline Inception:  $BASE_INC"
echo "  Baseline Expiration: $BASE_EXP"
assert_not_empty "Baseline SOA serial captured" "$BASE_SERIAL" "Serial"

step "Simulate Clock Skew / Outage Condition: Inject Future valid_begin (+2 Hours)"
sql_command "UPDATE zone SET valid_begin = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 2 HOUR) WHERE name = 'dnssec-gatekeeper-guard.com';"

step "Trigger transaction update in DIM (queue create_rr in outputupdate)"
ndcli create rr "probe.dnssec-gatekeeper-guard.com." TXT "gatekeeper test probe" -q

echo "  Waiting 4s for pdns-output to process and enforce DnssecGatekeeper refusal..."
sleep 4

step "Verify PowerDNS Integrity: Corrupt Update MUST BE REFUSED"
# 1. Probe record must NOT be in PowerDNS (transaction rolled back by pdns-output)
PROBE_QUERY=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "probe.dnssec-gatekeeper-guard.com." TXT +short)
assert_equals "PowerDNS rejected corrupt transaction (probe TXT not published)" "" "$PROBE_QUERY" "Query Output"

# 2. SOA serial in PowerDNS must remain unchanged at baseline
CURRENT_SERIAL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-gatekeeper-guard.com." SOA +short | awk '{print $3}')
assert_equals "PowerDNS SOA serial protected against corrupt modification" "$BASE_SERIAL" "$CURRENT_SERIAL" "SOA Serial"

# 3. Inception in PowerDNS must remain the original valid timestamp
CURRENT_SOA=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "dnssec-gatekeeper-guard.com." SOA +dnssec +noall +answer)
CURRENT_INC=$(echo "$CURRENT_SOA" | awk '$4 == "RRSIG" {print $10}')
assert_equals "PowerDNS RRSIG inception preserved at original valid timestamp" "$BASE_INC" "$CURRENT_INC" "Inception"

log_pass "DnssecGatekeeper successfully protected PowerDNS against future-dated signature publishing!"

step "Test Recovery: Restore Valid Timestamp in DIM"
sql_command "UPDATE zone SET valid_begin = DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE name = 'dnssec-gatekeeper-guard.com';"

step "Wait for pdns-output to process pending transaction and publish to PowerDNS"
RECOVERED=false
for _ in $(seq 1 20); do
    RECOVERED_PROBE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "probe.dnssec-gatekeeper-guard.com." TXT +short)
    if [ "$RECOVERED_PROBE" = '"gatekeeper test probe"' ]; then
        RECOVERED=true
        break
    fi
    sleep 1
done
assert_equals "pdns-output recovered and published probe record after valid timestamp restored" "true" "$RECOVERED"

step "Verify Post-Recovery Cryptographic Integrity"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" dnssec-gatekeeper-guard.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed after Gatekeeper recovery!"
fi
log_pass "Zone cryptographic validity intact post-recovery."

step "Cleanup"
delete_zone_all_views dnssec-gatekeeper-guard.com

log_pass "Test dnssec-gatekeeper-refusal completed successfully."
