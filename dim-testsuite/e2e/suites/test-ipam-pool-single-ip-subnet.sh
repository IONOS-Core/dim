#!/bin/bash
#
# suites/test-ipam-pool-single-ip-subnet.sh
# End-to-end test validating single-host (/32 IPv4 and /128 IPv6) subnet assignments to pools.
#
# Threat Model (Anycast Service IP and Single-Host Tracking):
# In modern data center networks, Anycast service IPs (e.g. BGP-announced DNS, NTP, and load-balancer
# virtual IPs) as well as external routed hosts require allocating individual /32 IPv4 (or /128 IPv6)
# subnets assigned to dedicated service pools.
#
# However, DIM hardcodes an artificial restriction in dim/dim/models/ip.py:455:
#   'Creating Subnet blocks with only one IP addresss is not allowed'
#
# When an administrator attempts to assign a /32 subnet to a pool, DIM aborts with an error,
# forcing operators to use inaccurate /31 workarounds or unmanaged IP allocations.
#
# Expected Behavior:
# DIM should permit creating and assigning /32 subnets to pools to accurately track
# individual service host routes.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL_ANYCAST="anycast-service-pool"
CONTAINER_NET="198.51.100.0/24"
HOST_SUBNET="198.51.100.53/32"

cleanup_single_ip() {
    ndcli -q delete pool "$POOL_ANYCAST" 2>/dev/null || true
    ndcli -q delete container "$CONTAINER_NET" 2>/dev/null || true
}
trap cleanup_single_ip EXIT INT TERM

step "Clean up previous runs"
cleanup_single_ip

step "Create container and service pool"
ndcli --quiet create container "$CONTAINER_NET"
ndcli --quiet create pool "$POOL_ANYCAST"

step "Test 1: Attempt adding /32 host subnet to pool"
set +e
ADD_OUT=$(ndcli modify pool "$POOL_ANYCAST" add subnet "$HOST_SUBNET" 2>&1)
ADD_EXIT=$?
set -e

echo "  Output from adding /32 subnet to pool:"
echo "  $ADD_OUT"

# Assertion: DIM SHOULD allow /32 subnets in pools for service IPAM
if echo "$ADD_OUT" | grep -q "with only one IP addresss is not allowed"; then
    log_fail "DEFECT DEMONSTRATED: DIM rejected /32 subnet assignment with hardcoded 'only one IP addresss' error!"
fi

assert_exit_code "Add /32 subnet to pool succeeds" 0 "$ADD_EXIT" "$ADD_OUT"
log_pass "/32 host subnet successfully assigned to pool."

step "Cleanup"
cleanup_single_ip
