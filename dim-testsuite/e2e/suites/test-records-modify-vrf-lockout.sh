#!/bin/bash
#
# suites/test-records-modify-vrf-lockout.sh
# End-to-end test validating Resource Record modification under multi-tenant VRFs (Layer3domains).
#
# Threat Model (The VRF Modify-RR Catch-22):
# When multiple Layer3domains (VRFs) exist with overlapping IP space, attempting to modify
# a DNS record (such as updating TTL or adding a comment via 'ndcli modify rr') causes DIM
# to fail with:
#   'ERROR - A layer3domain is needed'
#
# However, ndcli's CLI grammar for 'modify rr' does not accept the 'layer3domain' parameter,
# rejecting it with:
#   'ERROR - Unparsed tokens: layer3domain ...'
#
# This leaves administrators in an impossible Catch-22 where existing DNS records cannot be
# modified, updated, or maintained without deleting and re-creating them.
#
# Expected Behavior:
# 1. Modifying a DNS record's attributes (TTL, comment) belongs to the DNS zone/view and should
#    not fail due to ambiguous IPAM Layer3domains.
# 2. If Layer3domain disambiguation is required, the CLI must accept the layer3domain parameter.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="vrf-lockout-eval.com"
L3_A="vrf-tenant-alpha"
L3_B="vrf-tenant-beta"
OVERLAP_IP="10.88.1.50"

cleanup_vrf_lockout() {
    delete_zone_all_views "$ZONE"
    ndcli -q modify pool "pool-beta" remove subnet "10.88.1.0/24" 2>/dev/null || true
    ndcli -q modify pool "pool-alpha" remove subnet "10.88.1.0/24" 2>/dev/null || true
    ndcli -q delete pool "pool-beta" 2>/dev/null || true
    ndcli -q delete pool "pool-alpha" 2>/dev/null || true
    ndcli -q delete container "10.88.0.0/16" layer3domain "$L3_B" 2>/dev/null || true
    ndcli -q delete container "10.88.0.0/16" layer3domain "$L3_A" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_B" 2>/dev/null || true
    ndcli -q delete layer3domain "$L3_A" 2>/dev/null || true
}
trap cleanup_vrf_lockout EXIT INT TERM

step "Clean up previous runs"
cleanup_vrf_lockout

step "Create isolated Layer3domains and overlapping subnet pools"
ndcli --quiet create layer3domain "$L3_A" type vrf rd 0:301
ndcli --quiet create layer3domain "$L3_B" type vrf rd 0:302

ndcli --quiet create container "10.88.0.0/16" layer3domain "$L3_A"
ndcli --quiet create pool "pool-alpha" layer3domain "$L3_A"
ndcli --quiet modify pool "pool-alpha" add subnet "10.88.1.0/24"

ndcli --quiet create container "10.88.0.0/16" layer3domain "$L3_B"
ndcli --quiet create pool "pool-beta" layer3domain "$L3_B"
ndcli --quiet modify pool "pool-beta" add subnet "10.88.1.0/24" --allow-overlap

step "Create zone and allocate identical IP in both VRFs"
ndcli --quiet create zone "$ZONE"
ndcli --quiet create rr "host-a.${ZONE}." A "$OVERLAP_IP" layer3domain "$L3_A"
ndcli --quiet create rr "host-b.${ZONE}." A "$OVERLAP_IP" layer3domain "$L3_B" --allow-overlap

step "Test 1: Modify record TTL without specifying layer3domain"
set +e
MOD_OUT=$(ndcli modify rr "host-a.${ZONE}." A "$OVERLAP_IP" --ttl 60 2>&1)
MOD_EXIT=$?
set -e

echo "  Output from 'ndcli modify rr' without layer3domain:"
echo "  $MOD_OUT"

step "Test 2: If rejected, attempt supplying layer3domain parameter to CLI"
set +e
CLI_OUT=$(ndcli modify rr "host-a.${ZONE}." A "$OVERLAP_IP" layer3domain "$L3_A" --ttl 60 2>&1)
CLI_EXIT=$?
set -e

echo "  Output from 'ndcli modify rr' with layer3domain (exit $CLI_EXIT):"
echo "  $CLI_OUT"

# Assert whether the Catch-22 defect is present
if echo "$MOD_OUT" | grep -q "A layer3domain is needed" && [ "$CLI_EXIT" -ne 0 ] && echo "$CLI_OUT" | grep -q "Unparsed tokens"; then
    log_fail "DEFECT DEMONSTRATED: Server demands layer3domain, but CLI rejects layer3domain option (Admin Lockout)!"
fi

assert_exit_code "Modify RR succeeds cleanly" 0 "$MOD_EXIT" "$MOD_OUT"
log_pass "Record TTL modified successfully without VRF lockout."

step "Cleanup"
cleanup_vrf_lockout
