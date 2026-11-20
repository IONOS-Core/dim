#!/bin/bash
#
# suites/test-ipam-bulk-allocation.sh
# E2E Test: Rapid bulk IP allocation, reverse PTR consistency, selective release, and hole filling
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="bulk-alloc-pool"
CONTAINER="192.168.0.0/16"
SUBNET="192.168.190.0/24"
ZONE="bulk-alloc.com"
REV_ZONE="190.168.192.in-addr.arpa"
BATCH_SIZE=20

cleanup_bulk() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_ZONE"
    ndcli modify pool "$POOL" remove subnet "$SUBNET" --force -q 2>/dev/null || true
    ndcli delete pool "$POOL" -q 2>/dev/null || true
    ndcli delete container "$CONTAINER" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_bulk

step "Create IPAM Container, Pool, and /24 Subnet ($SUBNET)"
ndcli create container "$CONTAINER" -q
ndcli create pool "$POOL" -q
ndcli modify pool "$POOL" add subnet "$SUBNET" -q
ndcli create zone "$ZONE" -q
ensure_output_sync "$ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: Rapid bulk allocation of $BATCH_SIZE hosts from pool"
for i in $(seq 1 "$BATCH_SIZE"); do
    ndcli create rr "node${i}.${ZONE}." from "$POOL" -q
done

# Verify all 20 forward records exist
FWD_COUNT=$(ndcli list rrs "*.${ZONE}." A -H | grep -c "node" || true)
assert_equals "Forward A records created" "$BATCH_SIZE" "$FWD_COUNT" "Count"

# Verify all 20 reverse PTR records exist
REV_COUNT=$(ndcli list rrs "*.${REV_ZONE}." PTR -H | grep -c "${ZONE}." || true)
assert_equals "Reverse PTR records created" "$BATCH_SIZE" "$REV_COUNT" "Count"

# Verify all 20 allocated IPs are unique (no duplicate/collision)
UNIQUE_IPS=$(ndcli list rrs "*.${ZONE}." A -H | awk '{print $5}' | sort -u | wc -l)
assert_equals "All allocated IPs are unique" "$BATCH_SIZE" "$UNIQUE_IPS" "Count"

step "Wait for PowerDNS to receive batch records"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "node${BATCH_SIZE}.${ZONE}." A +short || true)
    if [ -n "$PDNS_ANS" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS received batch allocations" "true" "$SYNCED"

step "Test 2: Release first 10 hosts (holes created in pool space)"
for i in $(seq 1 10); do
    ndcli delete rr "node${i}.${ZONE}." a -q
done

FWD_REMAINING=$(ndcli list rrs "*.${ZONE}." A -H | grep -c "node" || true)
assert_equals "Forward records remaining after partial release" "10" "$FWD_REMAINING" "Count"

REV_REMAINING=$(ndcli list rrs "*.${REV_ZONE}." PTR -H | grep -c "${ZONE}." || true)
assert_equals "Reverse records remaining after partial release" "10" "$REV_REMAINING" "Count"

step "Test 3: Hole filling - Allocate 10 new hosts and assert immediate reclamation of freed slots"
for i in $(seq 1 10); do
    ndcli create rr "new${i}.${ZONE}." from "$POOL" -q
done

TOTAL_RECLAIMED=$(ndcli list rrs "*.${ZONE}." A -H | grep -c "new" || true)
assert_equals "New hosts allocated successfully" "10" "$TOTAL_RECLAIMED" "Count"

# Verify all 10 new hosts grabbed IPs from the freed .1 to .10 range (no IP leakage)
MAX_NEW_OCTET=$(ndcli list rrs "new*.${ZONE}." A -H | awk '{print $5}' | awk -F'.' '{print $4}' | sort -n | tail -n1)
echo "  Highest IP octet allocated to new batch: $MAX_NEW_OCTET"

LEAK_DETECTED=false
if [ "$MAX_NEW_OCTET" -gt 10 ]; then
    LEAK_DETECTED=true
fi
assert_equals "New allocations filled freed holes without leaking space" "false" "$LEAK_DETECTED"

step "Cleanup"
cleanup_bulk

log_pass "Bulk allocation, reverse consistency, and hole-filling test passed."
