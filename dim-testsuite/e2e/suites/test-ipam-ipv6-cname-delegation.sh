#!/bin/bash
#
# suites/test-ipam-ipv6-cname-delegation.sh
# E2E Test: IPv6 reverse CNAME delegation, 32-nibble validation, and chained PTR resolution
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_v6_cname() {
    delete_zone_all_views 1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa
    delete_zone_all_views 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa
}

step "Clean up previous runs"
cleanup_v6_cname

step "Create ISP and Customer IPv6 reverse zones"
ndcli create zone 1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa --quiet
ndcli create zone 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa --quiet
ensure_output_sync 1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa
ensure_output_sync 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa

step "Test 1: Incomplete nibble validation - PTR with fewer than 32 total nibbles must fail"
set +e
# 16 nibbles instead of 20 in a 12-nibble zone -> 28 total nibbles != 32
INCOMPLETE_OUT=$(ndcli modify zone 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa create rr "2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0" PTR customer-app.external.org. 2>&1)
EXIT_INCOMPLETE=$?
set -e

assert_exit_code "Incomplete IPv6 PTR rejected" 1 "$EXIT_INCOMPLETE" "$INCOMPLETE_OUT"
assert_contains "Incomplete PTR error message" "Invalid PTR name" "$INCOMPLETE_OUT"

step "Test 2: Complete 32-nibble PTR created in customer reverse zone"
ndcli modify zone 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa create rr 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0 PTR customer-app.external.org. --quiet

# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
PTR_CREATED=$(ndcli list zone 9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa -H | grep customer-app.external.org. | wc -l)
assert_equals "Target PTR created in customer zone" "1" "$PTR_CREATED" "Count"

step "Test 3: Setup CNAME delegation in ISP reverse zone pointing to customer zone"
ndcli modify zone 1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa create rr 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0 CNAME 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.9.9.9.9.8.b.d.0.1.0.0.2.ip6.arpa. --quiet

# shellcheck disable=SC2126
CNAME_CREATED=$(ndcli list zone 1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa -H | grep "CNAME" | wc -l)
assert_equals "CNAME delegation created in ISP zone" "1" "$CNAME_CREATED" "Count"

step "Wait for PowerDNS to receive both reverse zones and verify chained resolution"
SYNCED=false
PDNS_ANS=""
for _ in $(seq 1 20); do
    PDNS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa. PTR +noall +answer)
    if echo "$PDNS_ANS" | grep -q customer-app.external.org.; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS resolved chained IPv6 reverse CNAME to PTR" "true" "$SYNCED"

step "Verify exact Answer Section components (CNAME + terminal PTR)"
PDNS_FULL_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.1.0.0.0.8.b.d.0.1.0.0.2.ip6.arpa. PTR +noall +answer)
assert_contains "Answer contains CNAME step" "CNAME" "$PDNS_FULL_ANS"
assert_contains "Answer contains terminal PTR record" customer-app.external.org. "$PDNS_FULL_ANS"

step "Cleanup"
cleanup_v6_cname

log_pass "IPv6 reverse CNAME delegation and chained resolution test passed."
