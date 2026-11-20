#!/bin/bash
#
# suites/test-zone-dname-subtree.sh
# End-to-end test verifying RFC 6672 DNAME subtree redirection and collision constraints.
# Proves that:
#   1. A DNAME record can be created in a zone to alias an entire subtree.
#   2. Creating records underneath an existing DNAME subtree is strictly forbidden.
#   3. Creating a DNAME when records already exist in the subtree is strictly forbidden.
#   4. DNAME records are cleanly published to PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="dname-test.com"

step "Clean up previous runs"
delete_zone_all_views "$ZONE"

step "Create zone: $ZONE"
ndcli create zone "$ZONE"
ensure_output_sync "$ZONE"

step "Test 1: Create valid DNAME record for subtree alias"
ndcli create rr "sub.${ZONE}." DNAME target.example.org. -q
log_pass "DNAME record created for sub.${ZONE}."

step "Test 2: Verify creating an RR underneath existing DNAME subtree is strictly blocked"
if ndcli create rr "host.sub.${ZONE}." A 10.1.1.1 2>/dev/null; then
    log_fail "Expected creating RR under DNAME subtree to fail, but it succeeded!"
else
    log_pass "Subtree RR creation correctly blocked by existing DNAME."
fi

step "Wait for PowerDNS to receive DNAME record"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "sub.${ZONE}." DNAME +noall +answer | grep -q "DNAME"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive DNAME record for sub.${ZONE}."
fi
log_pass "PowerDNS answers authoritative DNAME query for sub.${ZONE}."

step "Test 3: Delete DNAME and verify subtree records can now be created"
ndcli delete rr "sub.${ZONE}." DNAME target.example.org. -q
ndcli create rr "host.sub.${ZONE}." A 10.1.1.1 -q
log_pass "Subtree RR created successfully after DNAME removal."

step "Test 4: Verify creating DNAME is blocked when subtree records already exist"
if ndcli create rr "sub.${ZONE}." DNAME target.example.org. 2>/dev/null; then
    log_fail "Expected DNAME creation over existing subtree records to fail, but it succeeded!"
else
    log_pass "DNAME creation over existing subtree records correctly blocked."
fi

step "Cleanup"
delete_zone_all_views "$ZONE"

log_pass "Test zone-dname-subtree completed successfully."
