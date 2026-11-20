#!/bin/bash
#
# suites/test-ipam-ipv6-special-scopes.sh
# E2E Test: IPv6 special scopes (loopback ::1, link-local fe80::/10, multicast ff00::/8), single-IP container guard
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="v6-special-scopes.com"

cleanup_v6_special() {
    delete_zone_all_views "$ZONE"
    ndcli -q delete container "fe80::/10" 2>/dev/null || true
    ndcli -q delete container "ff00::/8" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_v6_special

step "Test 1: Guard - Single-IP (/128) container creation is strictly rejected"
set +e
LOOPBACK_CONTAINER_OUT=$(ndcli create container "::1/128" 2>&1)
EXIT_LOOPBACK=$?
set -e

assert_exit_code "Single-IP /128 container rejected" 1 "$EXIT_LOOPBACK" "$LOOPBACK_CONTAINER_OUT"
assert_contains "Single-IP error message" "Creating Container blocks with only one IP addresss is not allowed" "$LOOPBACK_CONTAINER_OUT"

step "Test 2: Create Link-Local (fe80::/10) and Multicast (ff00::/8) containers"
ndcli --quiet create container "fe80::/10"
ndcli --quiet create container "ff00::/8"

CONTAINER_LL=$(ndcli show container "fe80::/10" | grep '^status:' | cut -d':' -f2)
assert_equals "Link-local container status" "Container" "$CONTAINER_LL" "Status"

CONTAINER_MC=$(ndcli show container "ff00::/8" | grep '^status:' | cut -d':' -f2)
assert_equals "Multicast container status" "Container" "$CONTAINER_MC" "Status"

step "Test 3: Create AAAA records with link-local and multicast addresses in zone"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

ndcli --quiet create rr "llocal.${ZONE}." AAAA "fe80::1"
ndcli --quiet create rr "allnodes.${ZONE}." AAAA "ff02::1"

# Verify records exist in DIM
LL_IP=$(ndcli list rrs "llocal.${ZONE}." AAAA -H | awk '{print $5}')
assert_equals "Link-local AAAA record created" "fe80::1" "$LL_IP" "IPv6 Address"

MC_IP=$(ndcli list rrs "allnodes.${ZONE}." AAAA -H | awk '{print $5}')
assert_equals "Multicast AAAA record created" "ff02::1" "$MC_IP" "IPv6 Address"

step "Wait for PowerDNS to receive special-scope AAAA records"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_LL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "llocal.${ZONE}." AAAA +short || true)
    PDNS_MC=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "allnodes.${ZONE}." AAAA +short || true)
    if [ "$PDNS_LL" = "fe80::1" ] && [ "$PDNS_MC" = "ff02::1" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS serves special-scope AAAA records" "true" "$SYNCED"

step "Cleanup"
cleanup_v6_special

log_pass "IPv6 special scope handling and container boundary guard test passed."
