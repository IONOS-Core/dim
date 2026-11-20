#!/bin/bash
#
# suites/test-records-rfc7505-null-mx.sh
# End-to-end test verifying RFC 7505 "Null MX" exclusivity and CNAME apex constraints.
# Proves that:
#   1. RFC 7505 Null MX (MX 0 .) can be created for domains that reject email.
#   2. Adding any other MX when Null MX exists is strictly rejected.
#   3. Adding Null MX when other MX records exist is strictly rejected.
#   4. Creating a CNAME at the zone apex is rejected (RFC 1034 / 2181 compliance).
#   5. Creating records that collide with existing CNAMEs is rejected.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE_NULL_MX="nullmx-test.com"
ZONE_STD_MX="stdmx-test.com"

step "Clean up previous runs"
delete_zone_all_views "$ZONE_NULL_MX"
delete_zone_all_views "$ZONE_STD_MX"

step "Create zones"
ndcli create zone "$ZONE_NULL_MX" -q
ensure_output_sync "$ZONE_NULL_MX"
ndcli create zone "$ZONE_STD_MX" -q

step "Test 1: Create RFC 7505 Null MX record (preference 0, target .)"
ndcli create rr "${ZONE_NULL_MX}." MX 0 .
log_pass "Null MX record successfully created."

step "Verify Null MX Authoritative Resolution in PowerDNS"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_MX=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${ZONE_NULL_MX}." MX +short)
    if [ "$PDNS_MX" = "0 ." ]; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS authoritatively returns RFC 7505 Null MX" "true" "$SYNCED"

step "Test 2: Verify adding another MX record alongside Null MX is strictly blocked"
if ndcli create rr "${ZONE_NULL_MX}." MX 10 mail.example.com. 2>/dev/null; then
    log_fail "Expected adding normal MX alongside Null MX to fail, but it succeeded!"
else
    log_pass "Normal MX correctly rejected because Null MX already exists."
fi

step "Test 3: Verify adding Null MX when a normal MX exists is strictly blocked"
ndcli create rr "${ZONE_STD_MX}." MX 10 mail.example.com. -q

if ndcli create rr "${ZONE_STD_MX}." MX 0 . 2>/dev/null; then
    log_fail "Expected adding Null MX alongside existing normal MX to fail, but it succeeded!"
else
    log_pass "Null MX correctly rejected because normal MX already exists."
fi

step "Test 4: Verify CNAME at zone apex is strictly forbidden"
if ndcli create rr "${ZONE_NULL_MX}." CNAME fallback.other.com. 2>/dev/null; then
    log_fail "Expected CNAME at zone apex to fail, but it succeeded!"
else
    log_pass "CNAME at zone apex correctly rejected."
fi

step "Test 5: Verify CNAME coexistence restriction (no other record at same name)"
ndcli create rr "service.${ZONE_NULL_MX}." CNAME target.example.com. -q

# Attempting to add an A record at the same name must fail
if ndcli create rr "service.${ZONE_NULL_MX}." A 10.1.1.1 2>/dev/null; then
    log_fail "Expected A record collision with CNAME to fail, but it succeeded!"
else
    log_pass "A record colliding with CNAME correctly rejected."
fi

step "Cleanup"
delete_zone_all_views "$ZONE_NULL_MX"
delete_zone_all_views "$ZONE_STD_MX"

log_pass "Test records-rfc7505-null-mx completed successfully."
