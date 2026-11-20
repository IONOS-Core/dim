#!/bin/bash
#
# suites/test-dnssec-parent-child.sh
# End-to-end test verifying DNSSEC parent-child delegation and trust chain lifecycle.
# Proves that:
#   1. When DNSSEC is enabled on a child zone, matching DS records are automatically created in the parent zone.
#   2. DS records are synchronized to PowerDNS.
#   3. When the child zone is decommissioned or DNSSEC is disabled, DS records are automatically removed
#      from the parent zone (preventing bogus validation failures / stale trust anchors).
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

PARENT="trust-parent.com"
CHILD="secure-child.${PARENT}"

step "Clean up previous runs"
delete_zone_all_views "$CHILD"
delete_zone_all_views "$PARENT"

step "Create DNSSEC-signed parent zone: $PARENT"
ndcli create zone "$PARENT"
ensure_output_sync "$PARENT"
ndcli modify zone "$PARENT" dnssec enable 8 ksk 2048 zsk 1024

step "Create DNSSEC-signed child zone: $CHILD"
ndcli create zone "$CHILD"
ensure_output_sync "$CHILD"
ndcli modify zone "$CHILD" dnssec enable 8 ksk 2048 zsk 1024

step "Verify DS record was automatically created in parent zone"
DS_COUNT_PARENT=$(ndcli list rrs "${CHILD}." DS -H | wc -l)
echo "  DS records found for ${CHILD} in parent: $DS_COUNT_PARENT"

if [ "$DS_COUNT_PARENT" -lt 1 ]; then
    log_fail "No DS record was created in parent zone $PARENT for child $CHILD!"
fi
log_pass "DS record automatically generated in parent zone $PARENT."

step "Wait for PowerDNS to receive parent zone with child DS record"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "${CHILD}." DS +noall +answer | grep -q "IN.*DS"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive DS record for ${CHILD} in parent zone."
fi
log_pass "PowerDNS authoritative server serves valid DS delegation for ${CHILD}."

step "Decommission child zone and verify DS record removal from parent"
delete_zone_all_views "$CHILD"

DS_COUNT_AFTER=$(ndcli list rrs "${CHILD}." DS -H 2>/dev/null | wc -l)
echo "  DS records remaining in parent after child zone delete: $DS_COUNT_AFTER"

if [ "$DS_COUNT_AFTER" -gt 0 ]; then
    log_fail "Stale DS record remained in parent zone after child zone was deleted!"
fi
log_pass "DS delegation cleanly purged from parent zone upon child deletion."

step "Cleanup"
delete_zone_all_views "$PARENT"

log_pass "Test dnssec-parent-child completed successfully."
