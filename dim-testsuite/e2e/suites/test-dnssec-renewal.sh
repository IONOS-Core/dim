#!/bin/bash
#
# suites/test-dnssec-renewal.sh
# End-to-end test verifying validity renewal for both NSEC (plain) and NSEC3 zones.
# Ensures that both NSEC and NSEC3 zones receive updated validity windows in PowerDNS
# when validity renewal runs (guards against regressions where NSEC updates were omitted),
# and strictly asserts that renewed inceptions are not in the future.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE_NSEC="renewal-nsec.com"
ZONE_NSEC3="renewal-nsec3.com"

cleanup_renewal_test() {
    delete_zone_all_views "$ZONE_NSEC"
    delete_zone_all_views "$ZONE_NSEC3"
}
trap cleanup_renewal_test EXIT INT TERM

step "Clean up previous runs"
cleanup_renewal_test

step "Create NSEC zone: $ZONE_NSEC"
ndcli create zone "$ZONE_NSEC"
ensure_output_sync "$ZONE_NSEC"
ndcli modify zone "$ZONE_NSEC" dnssec enable 8 ksk 2048 zsk 1024
ndcli create rr "test.${ZONE_NSEC}." A 10.10.1.1

step "Create NSEC3 zone: $ZONE_NSEC3"
ndcli create zone "$ZONE_NSEC3"
ensure_output_sync "$ZONE_NSEC3"
ndcli modify zone "$ZONE_NSEC3" dnssec enable 8 ksk 2048 zsk 1024 nsec3 10 salted
ndcli create rr "test.${ZONE_NSEC3}." A 10.10.2.1

step "Wait for initial signing in PowerDNS"
for z in "$ZONE_NSEC" "$ZONE_NSEC3"; do
    SYNCED=false
    for _ in $(seq 1 20); do
        if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${z}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
            SYNCED=true
            break
        fi
        sleep 1
    done
    assert_equals "Initial signing active for $z" "true" "$SYNCED"
done

# Capture initial RRSIG expiration and inception for both zones
EXP_NSEC_PRE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')
EXP_NSEC3_PRE=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC3}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')

echo "  Initial Expirations:"
echo "    - NSEC:  $EXP_NSEC_PRE"
echo "    - NSEC3: $EXP_NSEC3_PRE"

step "Simulate aging by modifying valid_end in database and triggering renewal"
# Age both zones by setting valid_end to 1 hour from now in UTC
sql_command "UPDATE zone SET valid_end = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE name IN ('${ZONE_NSEC}', '${ZONE_NSEC3}');"

# Run manage_dim update-validity
/src/dim-testsuite/virtual_env/bin/manage_dim update-validity --verbose

step "Wait for pdns-output to process updates and renew signatures in PowerDNS"
for z in "$ZONE_NSEC" "$ZONE_NSEC3"; do
    PRE_EXP=$( [ "$z" = "$ZONE_NSEC" ] && echo "$EXP_NSEC_PRE" || echo "$EXP_NSEC3_PRE" )
    RENEWED=false
    for _ in $(seq 1 25); do
        CURRENT_EXP=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${z}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')
        if [ -n "$CURRENT_EXP" ] && [ "$CURRENT_EXP" != "$PRE_EXP" ]; then
            RENEWED=true
            break
        fi
        sleep 1
    done
    assert_equals "PowerDNS received renewed validity for $z" "true" "$RENEWED"
done

EXP_NSEC_POST=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')
INC_NSEC_POST=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $10}')

EXP_NSEC3_POST=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC3}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')
INC_NSEC3_POST=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NSEC3}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $10}')

echo "  Renewed Timestamps in PowerDNS:"
echo "    - NSEC:  exp=$EXP_NSEC_POST (was $EXP_NSEC_PRE), inc=$INC_NSEC_POST"
echo "    - NSEC3: exp=$EXP_NSEC3_POST (was $EXP_NSEC3_PRE), inc=$INC_NSEC3_POST"

NOW_EPOCH=$(date -u +%s)

# Hard Assertions for Validity Window and Inception Integrity:
for z in "$ZONE_NSEC" "$ZONE_NSEC3"; do
    INC_STR=$( [ "$z" = "$ZONE_NSEC" ] && echo "$INC_NSEC_POST" || echo "$INC_NSEC3_POST" )
    INC_EPOCH=$(date -u -d "${INC_STR:0:4}-${INC_STR:4:2}-${INC_STR:6:2} ${INC_STR:8:2}:${INC_STR:10:2}:${INC_STR:12:2} UTC" +%s)

    # 1. Inception must NOT be in the future
    if [ "$INC_EPOCH" -gt "$NOW_EPOCH" ]; then
        log_fail "FATAL: Renewed inception $INC_STR for $z is in the future (+$((INC_EPOCH - NOW_EPOCH))s)!"
    fi
    log_pass "Renewed inception for $z is in the past ($INC_STR <= now)."

    # 2. RFC 6781 Backdating verified
    SKEW_DIFF=$((NOW_EPOCH - INC_EPOCH))
    if [ "$SKEW_DIFF" -lt 1800 ]; then
        log_fail "RFC 6781 Backdating violated for $z: Inception only ${SKEW_DIFF}s in past (expected >= 1800s)."
    fi
    log_pass "Renewed inception for $z conforms to RFC 6781 backdating (~3600s)."

    # 3. Cryptographic integrity verified
    if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$z" "@${PDNS_ADDRESS:-127.1.1.1}"; then
        log_fail "Cryptographic verification failed for $z after validity renewal!"
    fi
    log_pass "Cryptographic validation passed for renewed zone $z."
done

step "Cleanup"
cleanup_renewal_test

log_pass "Test dnssec-renewal completed successfully for both NSEC and NSEC3 zones."
