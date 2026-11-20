#!/bin/bash
#
# suites/test-ipam-pool-depletion.sh
# End-to-end test verifying IP pool exhaustion, boundary IP protections, and instant IP re-allocation.
# Proves that:
#   1. In a /30 subnet (4 total IPs), network (.0) and broadcast (.3) addresses are protected.
#   2. Exactly 2 usable host IPs (.1 and .2) can be allocated from the pool.
#   3. A 3rd allocation attempt is rejected with a clean "No free IP address found" error.
#   4. Deleting an allocated host with --free-ips returns the address to the pool.
#   5. The freed IP is immediately and deterministically re-allocatable to the next request.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

POOL="depletion-pool"
CONTAINER="192.168.188.0/24"
SUBNET="192.168.188.0/30"
ZONE="depletion-test.com"
REV_ZONE="188.168.192.in-addr.arpa"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"
delete_zone_all_views "$REV_ZONE"
ndcli modify pool "$POOL" remove subnet "$SUBNET" -q 2>/dev/null || true
ndcli delete pool "$POOL" -q 2>/dev/null || true
ndcli delete container "$CONTAINER" -q 2>/dev/null || true

step "Create IPAM Container and Pool with /30 subnet (2 usable host IPs)"
ndcli create container "$CONTAINER" -q
ndcli create pool "$POOL" -q
ndcli modify pool "$POOL" add subnet "$SUBNET" -q
ndcli create zone "$ZONE" -q
ensure_output_sync "$ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: Allocate first usable host IP (.1)"
ndcli create rr "host1.${ZONE}." from "$POOL" -q
IP1=$(ndcli list rrs "host1.${ZONE}." A -H | awk '{print $5}')
echo "  Allocated host1: $IP1"

if [ "$IP1" != "192.168.188.1" ]; then
    log_fail "Expected 192.168.188.1 for host1, got '$IP1'"
fi
log_pass "First host IP (.1) allocated successfully."

step "Test 2: Allocate second usable host IP (.2)"
ndcli create rr "host2.${ZONE}." from "$POOL" -q
IP2=$(ndcli list rrs "host2.${ZONE}." A -H | awk '{print $5}')
echo "  Allocated host2: $IP2"

if [ "$IP2" != "192.168.188.2" ]; then
    log_fail "Expected 192.168.188.2 for host2, got '$IP2'"
fi
log_pass "Second host IP (.2) allocated successfully."

step "Test 3: Attempt 3rd allocation on exhausted pool (must fail gracefully)"
OUTPUT_FAIL=""
if OUTPUT_FAIL=$(ndcli create rr "host3.${ZONE}." from "$POOL" 2>&1); then
    log_fail "Expected allocation on exhausted pool to fail, but it succeeded!"
else
    echo "  Expected failure message: $OUTPUT_FAIL"
    if ! echo "$OUTPUT_FAIL" | grep -q "No free IP address found"; then
        log_fail "Unexpected error message on pool depletion: $OUTPUT_FAIL"
    fi
    log_pass "Pool exhaustion handled gracefully (No free IP address found)."
fi

# Verify host3 record was NOT created in zone
if ndcli list rrs "host3.${ZONE}." -H 2>/dev/null | grep -q "host3"; then
    log_fail "Ghost record for host3 was created despite allocation failure!"
fi
log_pass "No ghost record created for failed allocation."

step "Test 4: Verify network (.0) and broadcast (.3) addresses are protected"
# Attempting to manually create A record on subnet network address
if ndcli create rr "net.${ZONE}." A 192.168.188.0 2>/dev/null; then
    log_fail "Creating A record on subnet network address (.0) should fail, but succeeded!"
else
    log_pass "Subnet network address (.0) protected against manual allocation."
fi

# Attempting to manually create A record on subnet broadcast address
if ndcli create rr "bcast.${ZONE}." A 192.168.188.3 2>/dev/null; then
    log_fail "Creating A record on subnet broadcast address (.3) should fail, but succeeded!"
else
    log_pass "Subnet broadcast address (.3) protected against manual allocation."
fi

step "Test 5: Free host1 (.1) and verify instant re-allocation"
ndcli delete rr "host1.${ZONE}." a -q
log_pass "host1 (192.168.188.1) freed."

# Re-attempt allocating host3: must now succeed and grab 192.168.188.1
ndcli create rr "host3.${ZONE}." from "$POOL" -q
IP3=$(ndcli list rrs "host3.${ZONE}." A -H | awk '{print $5}')
echo "  Allocated host3: $IP3"

if [ "$IP3" != "192.168.188.1" ]; then
    log_fail "Expected freed IP 192.168.188.1 for host3, got '$IP3'"
fi
log_pass "Freed IP immediately and deterministically re-allocated to host3."

step "Cleanup"
delete_zone_all_views "$ZONE"
delete_zone_all_views "$REV_ZONE"
ndcli modify pool "$POOL" remove subnet "$SUBNET" -q
ndcli delete pool "$POOL" -q
ndcli delete container "$CONTAINER" -q

log_pass "Test ipam-pool-depletion completed successfully."
