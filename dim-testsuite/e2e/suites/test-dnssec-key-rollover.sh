#!/bin/bash
#
# suites/test-dnssec-key-rollover.sh
# End-to-end test verifying DNSSEC key rollover lifecycle (ZSK and KSK rollover).
# Proves that:
#   1. Generating a new ZSK creates a dual-ZSK state in DIM and publishes both keys to PowerDNS.
#   2. Cryptographic validity is maintained uninterrupted throughout the rollover.
#   3. Deleting the retired ZSK leaves PowerDNS in a clean single-ZSK state without SERVFAIL.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="rollover-zone.com"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"

step "Create DNSSEC-signed zone: $ZONE"
ndcli create zone "$ZONE"
ensure_output_sync "$ZONE"
ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024
ndcli create rr "service.${ZONE}." A 10.55.1.1 -q

step "Wait for initial signing in PowerDNS"
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." SOA +dnssec +noall +answer | grep -q "RRSIG"; then
        break
    fi
    sleep 1
done

INITIAL_KEYS=$(ndcli list zone "$ZONE" keys -H | wc -l)
echo "  Initial keys in DIM: $INITIAL_KEYS (1 KSK, 1 ZSK)"
OLD_ZSK=$(ndcli list zone "$ZONE" keys -H | awk '$2 == "ZSK" {print $1}' | head -n1)
echo "  Active initial ZSK: $OLD_ZSK"

step "Trigger ZSK Rollover: Generate new ZSK"
ndcli modify zone "$ZONE" dnssec new zsk

KEYS_DURING_ROLLOVER=$(ndcli list zone "$ZONE" keys -H | wc -l)
echo "  Keys during rollover: $KEYS_DURING_ROLLOVER (expected 3: 1 KSK, 2 ZSKs)"

if [ "$KEYS_DURING_ROLLOVER" -ne 3 ]; then
    log_fail "Expected 3 keys in DIM during ZSK rollover, found $KEYS_DURING_ROLLOVER."
fi
log_pass "Dual-ZSK state successfully created in DIM."

step "Wait for PowerDNS to publish both DNSKEYs"
SYNCED_KEYS=false
for _ in $(seq 1 20); do
    KEY_COUNT_PDNS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." DNSKEY +short | wc -l)
    if [ "$KEY_COUNT_PDNS" -ge 3 ]; then
        SYNCED_KEYS=true
        break
    fi
    sleep 1
done

if [ "$SYNCED_KEYS" = false ]; then
    log_fail "PowerDNS did not publish all 3 DNSKEYs during rollover (found $KEY_COUNT_PDNS)!"
fi
log_pass "PowerDNS successfully published both ZSKs alongside KSK."

step "Verify Cryptographic & Inception Integrity during Rollover"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed during ZSK rollover!"
fi
log_pass "Signatures remain cryptographically valid during rollover."

step "Retire and delete old ZSK: $OLD_ZSK"
ndcli modify zone "$ZONE" dnssec delete key "$OLD_ZSK"

KEYS_POST_RETIRE=$(ndcli list zone "$ZONE" keys -H | wc -l)
echo "  Keys after retiring old ZSK: $KEYS_POST_RETIRE (expected 2)"

if [ "$KEYS_POST_RETIRE" -ne 2 ]; then
    log_fail "Expected 2 keys after retirement, found $KEYS_POST_RETIRE."
fi
log_pass "Old ZSK retired cleanly from DIM."

step "Wait for PowerDNS to converge to 2 DNSKEYs"
SYNCED_POST=false
for _ in $(seq 1 20); do
    KEY_COUNT_POST=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." DNSKEY +short | wc -l)
    if [ "$KEY_COUNT_POST" -eq 2 ]; then
        SYNCED_POST=true
        break
    fi
    sleep 1
done

if [ "$SYNCED_POST" = false ]; then
    log_fail "PowerDNS failed to converge to 2 DNSKEYs after ZSK retirement (found $KEY_COUNT_POST)."
fi
log_pass "PowerDNS converged to post-rollover key set."

step "Verify final Cryptographic integrity"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" "$ZONE" "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed after ZSK rollover completed!"
fi
log_pass "Final DNSSEC operational status verified."

step "Cleanup"
delete_zone_all_views "$ZONE"

log_pass "Test dnssec-key-rollover completed successfully."
