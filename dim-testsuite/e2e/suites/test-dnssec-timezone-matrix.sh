#!/bin/bash
#
# suites/test-dnssec-timezone-matrix.sh
# End-to-end test verifying DNSSEC inception and validity renewal across multiple database timezones.
#
# Regression test for non-UTC database timezones and DNSSEC validity windows:
# If MariaDB runs in a non-UTC time_zone (e.g. Europe/Berlin, +1h CET),
# reading TIMESTAMP columns without session UTC enforcement can cause
# RRSIG inception timestamps to be generated in the future, resulting in
# validation outages (SERVFAIL) on validating resolvers.
#
# This test rigorously verifies that under:
#   1. Europe/Berlin (CET/CEST - positive integer offset)
#   2. +05:30 (Asia/Kolkata - half-hour offset)
#   3. -05:00 (America/New_York - negative offset)
# Both initial signing AND validity renewal (manage_dim update-validity) produce
# strictly valid UTC RRSIG inceptions (never in the future) and cryptographically valid records.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

# Cleanup trap: ALWAYS restore MariaDB timezone to SYSTEM on exit or interruption
cleanup_mariadb_tz() {
    echo -e "\n==> Restoring MariaDB global timezone to SYSTEM"
    mysql -h127.0.0.1 -P3307 -uroot -e "SET GLOBAL time_zone = 'SYSTEM';"
}
trap cleanup_mariadb_tz EXIT INT TERM

set_mariadb_tz() {
    local tz="$1"
    echo -e "\n------------------------------------------------------------------"
    echo -e " Setting MariaDB @@global.time_zone = '${tz}'"
    echo -e "------------------------------------------------------------------"
    mysql -h127.0.0.1 -P3307 -uroot -e "SET GLOBAL time_zone = '${tz}';"
    local active_tz
    active_tz=$(mysql -h127.0.0.1 -P3307 -uroot -N -e "SELECT @@global.time_zone;")
    assert_equals "MariaDB @@global.time_zone successfully applied" "$tz" "$active_tz" "Timezone"
}

iso_to_epoch() {
    local s="$1"
    # Format: YYYYMMDDHHMMSS in UTC
    date -u -d "${s:0:4}-${s:4:2}-${s:6:2} ${s:8:2}:${s:10:2}:${s:12:2} UTC" +%s
}

TIMEZONES=("Europe/Berlin" "+05:30" "-05:00")

for TZ_SETTING in "${TIMEZONES[@]}"; do
    CLEAN_TAG=$(echo "$TZ_SETTING" | sed 's/[+:-]//g' | tr '/' '-' | tr '[:upper:]' '[:lower:]')
    ZONE="tz-${CLEAN_TAG}-test.com"

    step "Running Timezone Matrix Evaluation: ${TZ_SETTING}"
    set_mariadb_tz "$TZ_SETTING"

    step "Clean up previous runs for ${ZONE}"
    delete_zone_all_views "$ZONE"

    step "Create DNSSEC signed zone: ${ZONE} under MariaDB timezone ${TZ_SETTING}"
    ndcli create zone "$ZONE"
    ensure_output_sync "$ZONE"
    ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
    ndcli create rr "www.${ZONE}." A 10.42.1.1
    ndcli create rr "mail.${ZONE}." MX 10 "mail.example.com."

    step "Wait for pdns-output to sign and publish zone to PowerDNS"
    SYNCED=false
    for _ in $(seq 1 25); do
        if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
            SYNCED=true
            break
        fi
        sleep 1
    done
    assert_equals "PowerDNS received signed zone under ${TZ_SETTING}" "true" "$SYNCED"

    step "Verify Initial RRSIG Inception and Expiration Plausibility"
    SOA_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer)
    EXP_STR=$(echo "$SOA_ANS" | awk '$4 == "RRSIG" {print $9}')
    INC_STR=$(echo "$SOA_ANS" | awk '$4 == "RRSIG" {print $10}')

    NOW_EPOCH=$(date -u +%s)
    INC_EPOCH=$(iso_to_epoch "$INC_STR")
    EXP_EPOCH=$(iso_to_epoch "$EXP_STR")

    echo "  Current UTC:    $(date -u -d "@${NOW_EPOCH}" '+%Y-%m-%d %H:%M:%S UTC')"
    echo "  RRSIG Inc:      $(date -u -d "@${INC_EPOCH}" '+%Y-%m-%d %H:%M:%S UTC') ($INC_STR)"
    echo "  RRSIG Exp:      $(date -u -d "@${EXP_EPOCH}" '+%Y-%m-%d %H:%M:%S UTC') ($EXP_STR)"

    # Hard Assertion 1: Inception MUST NOT be in the future
    if [ "$INC_EPOCH" -gt "$NOW_EPOCH" ]; then
        log_fail "FATAL REGRESSION: Inception $INC_STR is in the future (+$((INC_EPOCH - NOW_EPOCH))s) under DB timezone $TZ_SETTING!"
    fi
    log_pass "Initial inception is not in the future (INC <= NOW)."

    # Hard Assertion 2: RFC 6781 Backdating must be preserved (~3600s skew)
    SKEW_DIFF=$((NOW_EPOCH - INC_EPOCH))
    if [ "$SKEW_DIFF" -lt 1800 ]; then
        log_fail "RFC 6781 Backdating violated: Inception only ${SKEW_DIFF}s in the past (expected >= 1800s)."
    fi
    log_pass "RFC 6781 backdating verified (${SKEW_DIFF}s in past, expected ~3600s)."

    # Hard Assertion 3: Expiration must be strictly in future
    if [ "$EXP_EPOCH" -le "$NOW_EPOCH" ]; then
        log_fail "Expiration $EXP_STR is not in the future!"
    fi
    log_pass "Initial expiration is valid and in the future."

    step "Trigger Validity Renewal via manage_dim update-validity (Renewal Trigger)"
    # Simulate zone validity aging: set valid_end to 1 hour from now in DIM database
    sql_command "UPDATE zone SET valid_end = DATE_ADD(UTC_TIMESTAMP(), INTERVAL 1 HOUR) WHERE name = '${ZONE}';"

    # Run update-validity tool
    /src/dim-testsuite/virtual_env/bin/manage_dim update-validity --verbose "$ZONE"

    step "Wait for PowerDNS to receive renewed signatures"
    RENEWED=false
    for _ in $(seq 1 25); do
        CURRENT_EXP=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | awk '$4 == "RRSIG" {print $9}')
        if [ -n "$CURRENT_EXP" ] && [ "$CURRENT_EXP" != "$EXP_STR" ]; then
            RENEWED=true
            break
        fi
        sleep 1
    done
    assert_equals "PowerDNS received renewed signature after update-validity" "true" "$RENEWED"

    step "Verify Renewed Signature Timestamps (Post-Rekey Under ${TZ_SETTING})"
    RENEWED_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer)
    NEW_EXP_STR=$(echo "$RENEWED_ANS" | awk '$4 == "RRSIG" {print $9}')
    NEW_INC_STR=$(echo "$RENEWED_ANS" | awk '$4 == "RRSIG" {print $10}')

    NOW_POST=$(date -u +%s)
    NEW_INC_EPOCH=$(iso_to_epoch "$NEW_INC_STR")
    NEW_EXP_EPOCH=$(iso_to_epoch "$NEW_EXP_STR")

    echo "  Current UTC (Post): $(date -u -d "@${NOW_POST}" '+%Y-%m-%d %H:%M:%S UTC')"
    echo "  Renewed Inc:        $(date -u -d "@${NEW_INC_EPOCH}" '+%Y-%m-%d %H:%M:%S UTC') ($NEW_INC_STR)"
    echo "  Renewed Exp:        $(date -u -d "@${NEW_EXP_EPOCH}" '+%Y-%m-%d %H:%M:%S UTC') ($NEW_EXP_STR)"

    if [ "$NEW_INC_EPOCH" -gt "$NOW_POST" ]; then
        log_fail "FATAL REGRESSION: Renewed inception $NEW_INC_STR is in future (+$((NEW_INC_EPOCH - NOW_POST))s) under DB timezone $TZ_SETTING!"
    fi
    log_pass "Renewed inception is strictly in the past (INC <= NOW)."

    if [ "$NEW_EXP_EPOCH" -le "$EXP_EPOCH" ]; then
        log_fail "Renewed expiration $NEW_EXP_STR is not greater than original expiration $EXP_STR!"
    fi
    log_pass "Renewed expiration extended validity window successfully."

    step "Verify Full Cryptographic Zone Integrity with verify-dnssec-zone.py"
    if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
        log_fail "Cryptographic validation failed for ${ZONE} under MariaDB timezone ${TZ_SETTING}!"
    fi
    log_pass "Cryptographic integrity of ${ZONE} verified under MariaDB timezone ${TZ_SETTING}."

    step "Cleanup zone ${ZONE}"
    delete_zone_all_views "$ZONE"
done

log_pass "All Timezone Matrix scenarios passed cleanly without time shifts or future inceptions!"
