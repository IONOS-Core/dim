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


cleanup_ptr() {
    delete_zone_all_views ptrlife-test.com
    delete_zone_all_views 245.168.192.in-addr.arpa
}

step "Clean up previous runs"
cleanup_ptr

step "Create forward and reverse zones"
ndcli create zone ptrlife-test.com -q
ndcli create zone 245.168.192.in-addr.arpa -q
ensure_output_sync ptrlife-test.com
ensure_output_sync 245.168.192.in-addr.arpa

step "Test 1: First A record automatically creates matching PTR"
ndcli create rr "node1.ptrlife-test.com." A 192.168.245.10 -q

INITIAL_PTR=""
for _ in $(seq 1 20); do
    INITIAL_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.245.168.192.in-addr.arpa. PTR +short)
    if [ -n "$INITIAL_PTR" ]; then
        break
    fi
    sleep 1
done
assert_equals "Initial auto-PTR target in PowerDNS" "node1.ptrlife-test.com." "$INITIAL_PTR"

step "Test 2: Second A record on same IP must NOT overwrite PTR by default"
OUTPUT_SECOND=$(ndcli create rr "node2.ptrlife-test.com." A 192.168.245.10 2>&1)
echo "$OUTPUT_SECOND"

assert_contains "Second A record warning" "Not overwriting: 10.245.168.192.in-addr.arpa. PTR node1.ptrlife-test.com." "$OUTPUT_SECOND"

STILL_NODE1=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.245.168.192.in-addr.arpa. PTR +short)
assert_equals "PTR preserved pointing to node1" "node1.ptrlife-test.com." "$STILL_NODE1"

step "Test 3: Third A record with --overwrite-ptr explicitly changes PTR target"
ndcli create rr "node3.ptrlife-test.com." A 192.168.245.10 --overwrite-ptr -q

OVERWRITTEN_PTR=""
for _ in $(seq 1 20); do
    OVERWRITTEN_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.245.168.192.in-addr.arpa. PTR +short)
    if [ "$OVERWRITTEN_PTR" = "node3.ptrlife-test.com." ]; then
        break
    fi
    sleep 1
done
assert_equals "PTR updated to node3 via --overwrite-ptr" "node3.ptrlife-test.com." "$OVERWRITTEN_PTR"

step "Test 4: CNAME collision guard (cannot create CNAME where PTR exists)"
set +e
CNAME_ERR=$(ndcli create rr 10.245.168.192.in-addr.arpa. CNAME other.target.com. 2>&1)
EXIT_CODE=$?
set -e

assert_exit_code "CNAME over PTR collision" 1 "$EXIT_CODE" "$CNAME_ERR"
assert_contains "Collision error message" "cannot be created because other RRs with the same name or target exist" "$CNAME_ERR"

step "Test 5: Deleting current PTR owner (node3) cleanly purges PTR from PowerDNS"
ndcli delete rr "node3.ptrlife-test.com." A 192.168.245.10 -q

FINAL_PTR="placeholder"
for _ in $(seq 1 20); do
    FINAL_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.245.168.192.in-addr.arpa. PTR +short)
    if [ -z "$FINAL_PTR" ]; then
        break
    fi
    sleep 1
done
assert_equals "PTR purged from PowerDNS after deleting owner" "" "$FINAL_PTR"

step "Cleanup"
cleanup_ptr

log_pass "PTR lifecycle test passed."
