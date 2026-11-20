#!/bin/bash
#
# suites/test-records-trailing-dot.sh
# E2E Test: DNS zone origin expansion, absolute FQDN trailing-dot semantics, and relative name qualification
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="trailing-dot-eval.com"

cleanup_dots() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_dots

step "Create forward zone and ensure output sync"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

step "Test 1: Absolute FQDN target (ending with dot) preserves absolute destination"
ndcli --quiet create rr "cname-abs.${ZONE}." CNAME "external-service.org."
ndcli --quiet create rr "${ZONE}." MX 10 "mailgw-abs.external.org."

step "Test 2: Relative target (omitting trailing dot) auto-expands with zone origin"
ndcli --quiet create rr "cname-rel.${ZONE}." CNAME "local-service"
ndcli --quiet create rr "${ZONE}." MX 20 "local-mailgw"

step "Wait for PowerDNS to receive both absolute and relative records"
SYNCED=false
PDNS_ABS=""
PDNS_REL=""
for _ in $(seq 1 20); do
    PDNS_ABS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "cname-abs.${ZONE}." CNAME +short)
    PDNS_REL=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "cname-rel.${ZONE}." CNAME +short)
    if [ "$PDNS_ABS" = "external-service.org." ] && [ "$PDNS_REL" = "local-service.${ZONE}." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received CNAME records" "true" "$SYNCED"

step "Assert: PowerDNS CNAME resolutions match RFC origin expansion rules"
assert_equals "Absolute CNAME target preserved" "external-service.org." "$PDNS_ABS" "Target"
assert_equals "Relative CNAME target qualified with zone origin" "local-service.${ZONE}." "$PDNS_REL" "Target"

step "Assert: PowerDNS MX resolutions match RFC origin expansion rules"
PDNS_MX=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE}." MX +short)
echo "  PowerDNS MX answers:"
echo "$PDNS_MX"

assert_contains "PowerDNS contains absolute MX target" "10 mailgw-abs.external.org." "$PDNS_MX"
assert_contains "PowerDNS contains auto-expanded relative MX target" "20 local-mailgw.${ZONE}." "$PDNS_MX"

step "Cleanup"
cleanup_dots

log_pass "DNS trailing-dot FQDN expansion test passed."
