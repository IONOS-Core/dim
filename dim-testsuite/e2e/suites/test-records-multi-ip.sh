#!/bin/bash
#
# suites/test-records-multi-ip.sh
# E2E Test: Dual-Stack multi-IP A/AAAA round-robin records, dual reverse PTR synchronization, and selective deletion
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

FWD_ZONE="dual-stack-test.com"
REV_V4_ZONE="215.168.192.in-addr.arpa"
REV_V6_ZONE="f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa"
HOST="srv.${FWD_ZONE}"
V4_A="192.168.215.10"
V4_B="192.168.215.11"
V6_A="2001:db8:beef::10"
V6_B="2001:db8:beef::11"

cleanup_multi() {
    delete_zone_all_views "$FWD_ZONE"
    delete_zone_all_views "$REV_V4_ZONE"
    delete_zone_all_views "$REV_V6_ZONE"
}

step "Clean up previous runs"
cleanup_multi

step "Create Forward Zone, IPv4 Reverse Zone, and IPv6 Reverse Zone"
ndcli --quiet create zone "$FWD_ZONE"
ndcli --quiet create zone "$REV_V4_ZONE"
ndcli --quiet create zone "$REV_V6_ZONE"
ensure_output_sync "$FWD_ZONE"
ensure_output_sync "$REV_V4_ZONE"
ensure_output_sync "$REV_V6_ZONE"

step "Test 1: Create Dual-Stack Multi-IP records (2x A, 2x AAAA)"
ndcli --quiet create rr "$HOST." A "$V4_A"
ndcli --quiet create rr "$HOST." A "$V4_B"
ndcli --quiet create rr "$HOST." AAAA "$V6_A"
ndcli --quiet create rr "$HOST." AAAA "$V6_B"

# Verify all 4 forward records in DIM
V4_COUNT=$(ndcli list rrs "$HOST." A -H | grep -c "$V4_A\|$V4_B" || true)
assert_equals "IPv4 forward records count" "2" "$V4_COUNT" "Count"

V6_COUNT=$(ndcli list rrs "$HOST." AAAA -H | grep -c "$V6_A\|$V6_B" || true)
assert_equals "IPv6 forward records count" "2" "$V6_COUNT" "Count"

# Verify all 4 reverse PTR records in DIM
PTR_V4_COUNT=$(ndcli list rrs "*.${REV_V4_ZONE}." PTR -H | grep -c "$HOST." || true)
assert_equals "IPv4 reverse PTR records count" "2" "$PTR_V4_COUNT" "Count"

PTR_V6_COUNT=$(ndcli list rrs "*.${REV_V6_ZONE}." PTR -H | grep -c "$HOST." || true)
assert_equals "IPv6 reverse PTR records count" "2" "$PTR_V6_COUNT" "Count"

step "Wait for PowerDNS to receive all 4 forward and 4 reverse records"
SYNCED=false
for _ in $(seq 1 20); do
    P_V4=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." A +short || true)
    P_V6=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." AAAA +short || true)
    P_PTR4=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "10.${REV_V4_ZONE}." PTR +short || true)
    P_PTR6=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "0.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.${REV_V6_ZONE}." PTR +short || true)

    if [ "$(echo "$P_V4" | wc -l)" -eq 2 ] && [ "$(echo "$P_V6" | wc -l)" -eq 2 ] && \
       [ "$P_PTR4" = "$HOST." ] && [ "$P_PTR6" = "$HOST." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received all dual-stack forward and reverse records" "true" "$SYNCED"

step "Test 2: Selective Deletion - Delete only one IPv4 address ($V4_A)"
ndcli --quiet delete rr "$HOST." A "$V4_A"

sleep 2
# Deleted IPv4 must be gone from forward and reverse
V4_A_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." A +short | grep "$V4_A" || true)
assert_equals "Deleted IPv4 address removed from forward A answers" "" "$V4_A_ANS" "IP"

PTR4_A_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "10.${REV_V4_ZONE}." PTR +short || true)
assert_equals "Deleted IPv4 PTR record removed from reverse zone" "" "$PTR4_A_ANS" "PTR"

# Sibling IPv4 ($V4_B) must remain intact
V4_B_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." A +short | grep "$V4_B" || true)
assert_equals "Sibling IPv4 address preserved" "$V4_B" "$V4_B_ANS" "IP"

PTR4_B_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "11.${REV_V4_ZONE}." PTR +short || true)
assert_equals "Sibling IPv4 PTR preserved" "$HOST." "$PTR4_B_ANS" "PTR"

# Both IPv6 addresses must remain completely unaffected
V6_REMAINING=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." AAAA +short | wc -l)
assert_equals "IPv6 records untouched by IPv4 deletion" "2" "$V6_REMAINING" "Count"

step "Test 3: Full IPv4 deletion without specifying IP"
ndcli --quiet delete rr "$HOST." A

sleep 2
TOTAL_V4_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." A +short || true)
assert_equals "All IPv4 A records purged" "" "$TOTAL_V4_ANS" "A Records"

# IPv6 records still completely unaffected
V6_STILL_INTACT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$HOST." AAAA +short | wc -l)
assert_equals "IPv6 records still intact after full IPv4 purge" "2" "$V6_STILL_INTACT" "Count"

step "Cleanup"
cleanup_multi

log_pass "Dual-stack multi-IP and reverse PTR lifecycle test passed."
