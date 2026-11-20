#!/bin/bash
#
# suites/test-records-ptr-lifecycle.sh
# E2E Test: Reverse PTR lifecycle, overwrite protections, and CNAME exclusivity
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

FWD_ZONE="ptrlife-test.com"
REV_ZONE="245.168.192.in-addr.arpa"
IP="192.168.245.10"
PTR_NAME="10.${REV_ZONE}."

cleanup_ptr() {
    delete_zone_all_views "$FWD_ZONE"
    delete_zone_all_views "$REV_ZONE"
}

step "Clean up previous runs"
cleanup_ptr

step "Create forward and reverse zones"
ndcli create zone "$FWD_ZONE" -q
ndcli create zone "$REV_ZONE" -q
ensure_output_sync "$FWD_ZONE"
ensure_output_sync "$REV_ZONE"

step "Test 1: First A record automatically creates matching PTR"
ndcli create rr "node1.${FWD_ZONE}." A "$IP" -q

INITIAL_PTR=""
for _ in $(seq 1 20); do
    INITIAL_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_NAME" PTR +short)
    if [ -n "$INITIAL_PTR" ]; then
        break
    fi
    sleep 1
done
assert_equals "Initial auto-PTR target in PowerDNS" "node1.${FWD_ZONE}." "$INITIAL_PTR"

step "Test 2: Second A record on same IP must NOT overwrite PTR by default"
OUTPUT_SECOND=$(ndcli create rr "node2.${FWD_ZONE}." A "$IP" 2>&1)
echo "$OUTPUT_SECOND"

assert_contains "Second A record warning" "Not overwriting: ${PTR_NAME} PTR node1.${FWD_ZONE}." "$OUTPUT_SECOND"

STILL_NODE1=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_NAME" PTR +short)
assert_equals "PTR preserved pointing to node1" "node1.${FWD_ZONE}." "$STILL_NODE1"

step "Test 3: Third A record with --overwrite-ptr explicitly changes PTR target"
ndcli create rr "node3.${FWD_ZONE}." A "$IP" --overwrite-ptr -q

OVERWRITTEN_PTR=""
for _ in $(seq 1 20); do
    OVERWRITTEN_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_NAME" PTR +short)
    if [ "$OVERWRITTEN_PTR" = "node3.${FWD_ZONE}." ]; then
        break
    fi
    sleep 1
done
assert_equals "PTR updated to node3 via --overwrite-ptr" "node3.${FWD_ZONE}." "$OVERWRITTEN_PTR"

step "Test 4: CNAME collision guard (cannot create CNAME where PTR exists)"
set +e
CNAME_ERR=$(ndcli create rr "$PTR_NAME" CNAME other.target.com. 2>&1)
EXIT_CODE=$?
set -e

assert_exit_code "CNAME over PTR collision" 1 "$EXIT_CODE" "$CNAME_ERR"
assert_contains "Collision error message" "cannot be created because other RRs with the same name or target exist" "$CNAME_ERR"

step "Test 5: Deleting current PTR owner (node3) cleanly purges PTR from PowerDNS"
ndcli delete rr "node3.${FWD_ZONE}." A "$IP" -q

FINAL_PTR="placeholder"
for _ in $(seq 1 20); do
    FINAL_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_NAME" PTR +short)
    if [ -z "$FINAL_PTR" ]; then
        break
    fi
    sleep 1
done
assert_equals "PTR purged from PowerDNS after deleting owner" "" "$FINAL_PTR"

step "Cleanup"
cleanup_ptr

log_pass "PTR lifecycle test passed."
