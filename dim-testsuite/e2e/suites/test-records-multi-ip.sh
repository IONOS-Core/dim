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


cleanup_multi() {
    delete_zone_all_views dual-stack-test.com
    delete_zone_all_views 215.168.192.in-addr.arpa
    delete_zone_all_views f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa
}

step "Clean up previous runs"
cleanup_multi

step "Create Forward Zone, IPv4 Reverse Zone, and IPv6 Reverse Zone"
ndcli --quiet create zone dual-stack-test.com
ndcli --quiet create zone 215.168.192.in-addr.arpa
ndcli --quiet create zone f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa
ensure_output_sync dual-stack-test.com
ensure_output_sync 215.168.192.in-addr.arpa
ensure_output_sync f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa

step "Test 1: Create Dual-Stack Multi-IP records (2x A, 2x AAAA)"
ndcli --quiet create rr "srv.dual-stack-test.com." A 192.168.215.10
ndcli --quiet create rr "srv.dual-stack-test.com." A 192.168.215.11
ndcli --quiet create rr "srv.dual-stack-test.com." AAAA 2001:db8:beef::10
ndcli --quiet create rr "srv.dual-stack-test.com." AAAA 2001:db8:beef::11

# Verify all 4 forward records in DIM
V4_COUNT=$(ndcli list rrs "srv.dual-stack-test.com." A -H | grep -c "192.168.215.10\|192.168.215.11" || true)
assert_equals "IPv4 forward records count" "2" "$V4_COUNT" "Count"

V6_COUNT=$(ndcli list rrs "srv.dual-stack-test.com." AAAA -H | grep -c "2001:db8:beef::10\|2001:db8:beef::11" || true)
assert_equals "IPv6 forward records count" "2" "$V6_COUNT" "Count"

# Verify all 4 reverse PTR records in DIM
PTR_V4_COUNT=$(ndcli list rrs "*.215.168.192.in-addr.arpa." PTR -H | grep -c "srv.dual-stack-test.com." || true)
assert_equals "IPv4 reverse PTR records count" "2" "$PTR_V4_COUNT" "Count"

PTR_V6_COUNT=$(ndcli list rrs "*.f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa." PTR -H | grep -c "srv.dual-stack-test.com." || true)
assert_equals "IPv6 reverse PTR records count" "2" "$PTR_V6_COUNT" "Count"

step "Wait for PowerDNS to receive all 4 forward and 4 reverse records"
SYNCED=false
for _ in $(seq 1 20); do
    P_V4=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." A +short || true)
    P_V6=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." AAAA +short || true)
    P_PTR4=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "10.215.168.192.in-addr.arpa." PTR +short || true)
    P_PTR6=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "0.1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa." PTR +short || true)

    if [ "$(echo "$P_V4" | wc -l)" -eq 2 ] && [ "$(echo "$P_V6" | wc -l)" -eq 2 ] && \
       [ "$P_PTR4" = "srv.dual-stack-test.com." ] && [ "$P_PTR6" = "srv.dual-stack-test.com." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received all dual-stack forward and reverse records" "true" "$SYNCED"

step "Test 2: Selective Deletion - Delete only one IPv4 address (192.168.215.10)"
ndcli --quiet delete rr "srv.dual-stack-test.com." A 192.168.215.10

sleep 2
# Deleted IPv4 must be gone from forward and reverse
V4_A_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." A +short | grep 192.168.215.10 || true)
assert_equals "Deleted IPv4 address removed from forward A answers" "" "$V4_A_ANS" "IP"

PTR4_A_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "10.215.168.192.in-addr.arpa." PTR +short || true)
assert_equals "Deleted IPv4 PTR record removed from reverse zone" "" "$PTR4_A_ANS" "PTR"

# Sibling IPv4 (192.168.215.11) must remain intact
V4_B_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." A +short | grep 192.168.215.11 || true)
assert_equals "Sibling IPv4 address preserved" 192.168.215.11 "$V4_B_ANS" "IP"

PTR4_B_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "11.215.168.192.in-addr.arpa." PTR +short || true)
assert_equals "Sibling IPv4 PTR preserved" "srv.dual-stack-test.com." "$PTR4_B_ANS" "PTR"

# Both IPv6 addresses must remain completely unaffected
V6_REMAINING=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." AAAA +short | wc -l)
assert_equals "IPv6 records untouched by IPv4 deletion" "2" "$V6_REMAINING" "Count"

step "Test 3: Full IPv4 deletion without specifying IP"
ndcli --quiet delete rr "srv.dual-stack-test.com." A

sleep 2
TOTAL_V4_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." A +short || true)
assert_equals "All IPv4 A records purged" "" "$TOTAL_V4_ANS" "A Records"

# IPv6 records still completely unaffected
V6_STILL_INTACT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "srv.dual-stack-test.com." AAAA +short | wc -l)
assert_equals "IPv6 records still intact after full IPv4 purge" "2" "$V6_STILL_INTACT" "Count"

step "Cleanup"
cleanup_multi

log_pass "Dual-stack multi-IP and reverse PTR lifecycle test passed."
