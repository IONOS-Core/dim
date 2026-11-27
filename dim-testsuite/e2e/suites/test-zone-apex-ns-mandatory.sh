#!/bin/bash
#
# suites/test-zone-apex-ns-mandatory.sh
# End-to-end test validating the RFC 1034 / RFC 2181 mandatory apex NS record requirement.
#
# Threat Model (Lame Delegation Outage):
# In DNS, an authoritative zone MUST have at least one NS record at the zone apex.
# If an administrator creates a zone in DIM without a profile (or deletes all NS records),
# DIM generates only the SOA record and zero NS records.
#
# PowerDNS publishes the zone without NS records. When resolving nameservers query the zone,
# PowerDNS responds with NOERROR and 0 answer records (NODATA).
# Resolvers classify this as a LAME DELEGATION, caching the failure and rejecting all
# queries for the domain.
#
# Expected Behavior:
# DIM must prevent zones without apex NS records from being published to PowerDNS.
# Either creation without NS records must be rejected, or the primary nameserver from the SOA
# must automatically be created as an apex NS record.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_lame_apex() {
    delete_zone_all_views lame-apex-eval.com
}
trap cleanup_lame_apex EXIT INT TERM

step "Clean up previous runs"
cleanup_lame_apex

step "Create zone without profile in DIM: lame-apex-eval.com"
CREATE_OUT=$(ndcli create zone lame-apex-eval.com 2>&1)
echo "$CREATE_OUT"
ensure_output_sync lame-apex-eval.com

step "Wait for PowerDNS to receive authoritative zone"
SYNCED=false
for _ in $(seq 1 20); do
    SOA_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "lame-apex-eval.com." SOA +short)
    if [ -n "$SOA_ANS" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS received zone with SOA" "true" "$SYNCED"

step "Test 1: Check whether DIM populated an apex NS record"
# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
NS_COUNT_DIM=$(ndcli list rrs "lame-apex-eval.com." NS -H | grep -v "^INFO" | wc -l)
echo "  Apex NS records in DIM: $NS_COUNT_DIM"

step "Test 2: Query PowerDNS for apex NS records"
PDNS_NS_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "lame-apex-eval.com." NS +noall +answer)
echo "  PowerDNS NS answer section:"
echo "  $PDNS_NS_ANS"

# RFC 1034 / RFC 2181 Assertion:
# Authoritative zone MUST serve at least one apex NS record
if [ -z "$PDNS_NS_ANS" ]; then
    # Query authority section to demonstrate NODATA / Lame Delegation response
    AUTH_SECTION=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "lame-apex-eval.com." NS +noall +authority)
    echo "  PowerDNS returned NODATA with SOA in authority section:"
    echo "  $AUTH_SECTION"

    log_fail "DEFECT DEMONSTRATED: Zone published without apex NS records, causing LAME DELEGATION in PowerDNS!"
else
    log_pass "PowerDNS serves valid apex NS record."
fi

step "Cleanup"
cleanup_lame_apex
