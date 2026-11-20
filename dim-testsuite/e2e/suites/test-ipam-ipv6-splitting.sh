#!/bin/bash
#
# suites/test-ipam-ipv6-splitting.sh
# E2E Test: IPv6 hierarchical subnet splitting, most-specific reverse zone matching, and PTR resolution
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

CONTAINER_V6="2001:db8:a1b2::/48"
PARENT_REV="2.b.1.a.8.b.d.0.1.0.0.2.ip6.arpa"
CHILD_REV="0.0.1.${PARENT_REV}"
SUBNET_V6="2001:db8:a1b2:1000::/52"
POOL_V6="v6-split-pool"
FWD_ZONE="v6-split.com"
HOST_IP="2001:db8:a1b2:1000::42"

cleanup_v6_split() {
    delete_zone_all_views "$FWD_ZONE"
    delete_zone_all_views "$CHILD_REV"
    delete_zone_all_views "$PARENT_REV"
    ndcli modify pool "$POOL_V6" remove subnet "$SUBNET_V6" -q 2>/dev/null || true
    ndcli delete pool "$POOL_V6" -q 2>/dev/null || true
    ndcli delete container "$CONTAINER_V6" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_v6_split

step "Create IPv6 /48 Container and Subnet in dedicated Pool"
ndcli create container "$CONTAINER_V6" -q
ndcli create pool "$POOL_V6" -q
ndcli modify pool "$POOL_V6" add subnet "$SUBNET_V6" -q

step "Setup Forward Zone and Hierarchical Reverse Zones (Parent /48 and Specific Child /52)"
ndcli create zone "$FWD_ZONE" -q
ensure_output_sync "$FWD_ZONE"

ndcli create zone "$PARENT_REV" -q
ensure_output_sync "$PARENT_REV"

ndcli create zone "$CHILD_REV" -q
ensure_output_sync "$CHILD_REV"

step "Test 1: Create AAAA record and assert most-specific reverse zone matching"
ndcli create rr "service.${FWD_ZONE}." AAAA "$HOST_IP" -q

# Verify PTR was inserted into the CHILD reverse zone (not parent)
# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
CHILD_PTR=$(ndcli list zone "$CHILD_REV" -H | grep "service.${FWD_ZONE}." | wc -l)
assert_equals "PTR created in most-specific child reverse zone" "1" "$CHILD_PTR" "Count"

# shellcheck disable=SC2126
PARENT_PTR=$(ndcli list zone "$PARENT_REV" -H | grep "service.${FWD_ZONE}." | wc -l)
assert_equals "PTR not duplicated in parent reverse zone" "0" "$PARENT_PTR" "Count"

step "Wait for PowerDNS to receive forward and reverse zones"
# Calculate 32-nibble FQDN for 2001:db8:a1b2:1000::42
# Host part (17 nibbles): 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0
# Child zone (15 nibbles): 0.0.1.2.b.1.a.8.b.d.0.1.0.0.2.ip6.arpa
FULL_PTR_QUERY="2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.${CHILD_REV}."

SYNCED=false
PDNS_PTR=""
for _ in $(seq 1 20); do
    PDNS_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$FULL_PTR_QUERY" PTR +short)
    if [ "$PDNS_PTR" = "service.${FWD_ZONE}." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS resolves hierarchical IPv6 PTR" "true" "$SYNCED"

step "Test 2: Delete AAAA record and assert clean PTR purge from specific reverse zone"
ndcli delete rr "service.${FWD_ZONE}." AAAA "$HOST_IP" -q

PDNS_PTR_AFTER="placeholder"
for _ in $(seq 1 20); do
    PDNS_PTR_AFTER=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$FULL_PTR_QUERY" PTR +short)
    if [ -z "$PDNS_PTR_AFTER" ]; then
        break
    fi
    sleep 1
done
assert_equals "PTR purged from PowerDNS after AAAA deletion" "" "$PDNS_PTR_AFTER"

step "Cleanup"
cleanup_v6_split

log_pass "IPv6 hierarchical splitting and reverse lifecycle test passed."
