#!/bin/bash
#
# suites/test-ipam-pool-priorities.sh
# E2E Test: Multi-subnet pool priority ordering, spillover, and priority recovery
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="prio-eval-pool"
CONTAINER="192.168.250.0/23"
SUBNET_LOW="192.168.250.0/30"
SUBNET_HIGH="192.168.251.0/30"
ZONE="prio-eval.com"
REV_LOW="250.168.192.in-addr.arpa"
REV_HIGH="251.168.192.in-addr.arpa"

cleanup_prio() {
    delete_zone_all_views "$ZONE"
    delete_zone_all_views "$REV_LOW"
    delete_zone_all_views "$REV_HIGH"
    ndcli modify pool "$POOL" remove subnet "$SUBNET_LOW" --force -q 2>/dev/null || true
    ndcli modify pool "$POOL" remove subnet "$SUBNET_HIGH" --force -q 2>/dev/null || true
    ndcli delete pool "$POOL" -q 2>/dev/null || true
    ndcli delete container "$CONTAINER" -q 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_prio

step "Setup container, multi-subnet pool, and explicit allocator priorities"
ndcli create container "$CONTAINER" -q
ndcli create pool "$POOL" -q
ndcli modify pool "$POOL" add subnet "$SUBNET_LOW" -q
ndcli modify pool "$POOL" add subnet "$SUBNET_HIGH" -q

# Set explicit priority: SUBNET_HIGH = Priority 1, SUBNET_LOW = Priority 2
ndcli modify pool "$POOL" subnet "$SUBNET_HIGH" set prio 1 -q
ndcli modify pool "$POOL" subnet "$SUBNET_LOW" set prio 2 -q

ndcli create zone "$ZONE" -q
ensure_output_sync "$ZONE"

step "Test 1: Allocations strictly drain Priority 1 subnet first"
ndcli create rr "h1.${ZONE}." from "$POOL" -q
ndcli create rr "h2.${ZONE}." from "$POOL" -q

IP_H1=$(ndcli list rrs "h1.${ZONE}." A -H | awk '{print $5}')
IP_H2=$(ndcli list rrs "h2.${ZONE}." A -H | awk '{print $5}')

assert_equals "Host 1 allocated from Priority 1 subnet" "192.168.251.1" "$IP_H1" "IP"
assert_equals "Host 2 allocated from Priority 1 subnet" "192.168.251.2" "$IP_H2" "IP"

step "Test 2: Spillover to Priority 2 subnet when Priority 1 is exhausted"
ndcli create rr "h3.${ZONE}." from "$POOL" -q
IP_H3=$(ndcli list rrs "h3.${ZONE}." A -H | awk '{print $5}')

assert_equals "Host 3 spilled over to Priority 2 subnet" "192.168.250.1" "$IP_H3" "IP"

step "Test 3: Priority Recovery - Freeing IP in Priority 1 prioritizes it over Priority 2"
# Free h1 (192.168.251.1 in Priority 1 subnet)
ndcli delete rr "h1.${ZONE}." a -q

# Allocate new host h4: allocator must choose freed Priority 1 IP rather than continuing in Priority 2
ndcli create rr "h4.${ZONE}." from "$POOL" -q
IP_H4=$(ndcli list rrs "h4.${ZONE}." A -H | awk '{print $5}')

assert_equals "Host 4 reclaimed freed Priority 1 IP" "192.168.251.1" "$IP_H4" "IP"

step "Cleanup"
cleanup_prio

log_pass "Pool priority and allocation ordering test passed."
