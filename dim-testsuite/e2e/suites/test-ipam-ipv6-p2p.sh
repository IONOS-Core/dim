#!/bin/bash
#
# suites/test-ipam-ipv6-p2p.sh
# E2E Test: RFC 6164 IPv6 /127 inter-router point-to-point links, --no-default-reserve, and reverse PTR resolution
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_p2p() {
    delete_zone_all_views p2p-eval.com
    delete_zone_all_views f.f.f.f.e.f.a.c.8.b.d.0.1.0.0.2.ip6.arpa
    ndcli -q modify pool p2p-rfc6164-pool remove subnet 2001:db8:cafe:ffff::/127 --force 2>/dev/null || true
    ndcli -q delete pool p2p-rfc6164-pool 2>/dev/null || true
    ndcli -q delete container 2001:db8:cafe::/48 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_p2p

step "Create IPv6 /48 Container and Pool with RFC 6164 /127 subnet (--no-default-reserve)"
ndcli -q create container 2001:db8:cafe::/48
ndcli -q create pool p2p-rfc6164-pool
# RFC 6164 inter-router links require both ::0 and ::1 to be allocatable (--no-default-reserve)
ndcli -q modify pool p2p-rfc6164-pool add subnet 2001:db8:cafe:ffff::/127 --no-default-reserve
ndcli -q create zone p2p-eval.com
ensure_output_sync p2p-eval.com
ensure_output_sync f.f.f.f.e.f.a.c.8.b.d.0.1.0.0.2.ip6.arpa

step "Test 1: Allocate Router A endpoint (must receive ::0)"
ndcli -q create rr "rtr-a.p2p-eval.com." from p2p-rfc6164-pool

IP_A=$(ndcli list rrs "rtr-a.p2p-eval.com." AAAA -H | awk '{print $5}')
echo "  Allocated Router A: $IP_A"
assert_equals "Router A allocated lower link address (::0)" 2001:db8:cafe:ffff:: "$IP_A" "IPv6 Address"

step "Test 2: Allocate Router B endpoint (must receive ::1)"
ndcli -q create rr "rtr-b.p2p-eval.com." from p2p-rfc6164-pool

IP_B=$(ndcli list rrs "rtr-b.p2p-eval.com." AAAA -H | awk '{print $5}')
echo "  Allocated Router B: $IP_B"
assert_equals "Router B allocated upper link address (::1)" 2001:db8:cafe:ffff::1 "$IP_B" "IPv6 Address"

step "Test 3: Assert pool exhaustion on 3rd allocation request"
set +e
EXHAUST_OUT=$(ndcli create rr "rtr-c.p2p-eval.com." from p2p-rfc6164-pool 2>&1)
EXIT_EXHAUST=$?
set -e

assert_exit_code "3rd allocation on /127 pool rejected" 1 "$EXIT_EXHAUST" "$EXHAUST_OUT"
assert_contains "Pool exhausted message" "No free IP address found" "$EXHAUST_OUT"

step "Test 4: Verify PowerDNS resolves both router endpoints forward and reverse"
SYNCED=false
PDNS_A=""
PDNS_B=""
for _ in $(seq 1 20); do
    PDNS_A=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "rtr-a.p2p-eval.com." AAAA +short)
    PDNS_B=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "rtr-b.p2p-eval.com." AAAA +short)
    if [ "$PDNS_A" = 2001:db8:cafe:ffff:: ] && [ "$PDNS_B" = 2001:db8:cafe:ffff::1 ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received both RFC 6164 router endpoints" "true" "$SYNCED"

# Verify 32-nibble PTR resolution for ::0 (Router A)
PTR_A_QUERY="0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.f.f.f.f.e.f.a.c.8.b.d.0.1.0.0.2.ip6.arpa."
PDNS_PTR_A=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_A_QUERY" PTR +short)
assert_equals "PowerDNS resolves Router A ::0 PTR" "rtr-a.p2p-eval.com." "$PDNS_PTR_A" "PTR Target"

# Verify 32-nibble PTR resolution for ::1 (Router B)
PTR_B_QUERY="1.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.f.f.f.f.e.f.a.c.8.b.d.0.1.0.0.2.ip6.arpa."
PDNS_PTR_B=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_B_QUERY" PTR +short)
assert_equals "PowerDNS resolves Router B ::1 PTR" "rtr-b.p2p-eval.com." "$PDNS_PTR_B" "PTR Target"

step "Cleanup"
cleanup_p2p

log_pass "RFC 6164 IPv6 /127 point-to-point link test passed."
