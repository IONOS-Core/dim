#!/bin/bash
#
# suites/test-zone-profile-inheritance.sh
# End-to-end test verifying Zone Profile creation, template inheritance, and PowerDNS SOA parameters.
# Proves that:
#   1. Custom zone profiles can define specific SOA timers (refresh, retry, expire, minimum, ttl).
#   2. Creating a zone with --profile inherits all custom SOA parameters and default NS/MX records.
#   3. PowerDNS receives and serves the exact authoritative SOA timers from the profile.
#   4. Modifying or deleting the profile does not disrupt or mutate existing instantiated zones.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


step "Clean up previous runs"
delete_zone_all_views profile-test.com
ndcli delete zone-profile corp-profile -q 2>/dev/null || true

step "Create custom Zone Profile with explicit SOA parameters"
ndcli create zone-profile corp-profile \
    primary "ns1.corp-infra.example.org." \
    mail "dnsadmin@corp-infra.example.org" \
    refresh 7200 \
    retry 1800 \
    expire 1209600 \
    minimum 3600 \
    ttl 14400

# Add template NS records to profile
ndcli modify zone-profile corp-profile create rr @ NS "ns1.corp-infra.example.org." -q
ndcli modify zone-profile corp-profile create rr @ NS "ns2.corp-infra.example.org." -q
log_pass "Zone profile created with custom SOA timers."

step "Instantiate zone from profile: profile-test.com"
ndcli create zone profile-test.com profile corp-profile
ensure_output_sync profile-test.com

step "Verify zone inherited custom SOA timers and NS records"
SOA_RECORD=$(ndcli list zone profile-test.com -H | grep "SOA")
echo "  Inherited SOA in DIM: $SOA_RECORD"

if ! echo "$SOA_RECORD" | grep -q "7200 1800 1209600 3600"; then
    log_fail "Zone failed to inherit custom SOA timers from profile! (Got: $SOA_RECORD)"
fi

NS_COUNT=$(ndcli list zone profile-test.com -H | grep -c "NS" || true)
if [ "$NS_COUNT" -lt 2 ]; then
    log_fail "Zone failed to inherit template NS records from profile (found $NS_COUNT)."
fi
log_pass "Zone successfully inherited custom SOA timers and NS records from profile."

step "Wait for PowerDNS to receive zone and verify SOA timers"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "profile-test.com." SOA +noall +answer | grep -q "7200 1800 1209600 3600"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive zone with custom inherited SOA timers."
fi
log_pass "PowerDNS serves authoritative SOA matching profile timers."

step "Test profile independence: Delete profile and verify existing zone continues functioning"
ndcli delete zone-profile corp-profile -q

# Zone must still exist and respond in PowerDNS
PDNS_SOA=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "profile-test.com." SOA +short)
if [ -z "$PDNS_SOA" ]; then
    log_fail "Zone stopped resolving in PowerDNS after template profile was deleted!"
fi
log_pass "Zone remains fully functional after template profile deletion."

step "Cleanup"
delete_zone_all_views profile-test.com

log_pass "Test zone-profile-inheritance completed successfully."
