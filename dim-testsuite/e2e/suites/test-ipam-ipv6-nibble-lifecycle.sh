#!/bin/bash
#
# suites/test-ipam-ipv6-nibble-lifecycle.sh
# End-to-end test verifying IPv6 reverse DNS (ip6.arpa) nibble calculation and lifecycle.
# Proves that:
#   1. A /48 IPv6 reverse zone (nibble format) is properly handled by DIM and PowerDNS.
#   2. Creating an AAAA record automatically creates the 32-nibble PTR in ip6.arpa.
#   3. PowerDNS correctly resolves both forward AAAA and reverse PTR queries.
#   4. Deleting the AAAA record cleanly purges the reverse PTR from PowerDNS.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

# 2001:db8:beef::/48 -> f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa

step "Clean up previous runs"
delete_zone_all_views ipv6-test.com
delete_zone_all_views f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa

step "Create forward zone: ipv6-test.com"
ndcli create zone ipv6-test.com
ensure_output_sync ipv6-test.com

step "Create IPv6 reverse zone in nibble format: f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa"
ndcli create zone f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa
ensure_output_sync f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa

step "Create AAAA record with auto-PTR enabled"
OUTPUT=$(ndcli create rr "app1.ipv6-test.com." aaaa 2001:db8:beef::42 2>&1)
echo "$OUTPUT"

step "Verify PTR record was automatically generated in f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa"
PTR_LIST=$(ndcli list zone f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa -H)
echo "  Records in f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa:"
echo "$PTR_LIST"

if ! echo "$PTR_LIST" | grep -q "app1.ipv6-test.com."; then
    log_fail "PTR record for app1.ipv6-test.com was not created in f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa!"
fi
log_pass "Auto-PTR successfully created in IPv6 nibble zone."

# Full 32-nibble reverse FQDN (20 nibbles in record + 12 nibbles in /48 zone = 32 nibbles)
FULL_REV_NAME="2.4.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.0.f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa."

step "Wait for PowerDNS to receive forward and reverse zones"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "app1.ipv6-test.com." AAAA +noall +answer | grep -q 2001:db8:beef::42 && \
       dig "@${PDNS_ADDRESS:-127.1.1.1}" "$FULL_REV_NAME" PTR +short | grep -q app1.ipv6-test.com; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive forward AAAA and reverse PTR records for app1.ipv6-test.com."
fi
log_pass "PowerDNS answers authoritative AAAA and PTR queries for app1.ipv6-test.com."

PDNS_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$FULL_REV_NAME" PTR +short)
echo "  PowerDNS PTR answer: $PDNS_PTR"

if [[ "$PDNS_PTR" != *"app1.ipv6-test.com."* ]]; then
    log_fail "PowerDNS PTR query failed! Expected app1.ipv6-test.com., got '$PDNS_PTR'"
fi
log_pass "PowerDNS successfully resolved 32-nibble IPv6 PTR to app1.ipv6-test.com."

step "Delete AAAA record and verify automatic PTR cleanup"
ndcli delete rr "app1.ipv6-test.com." aaaa 2001:db8:beef::42 -q

sleep 2
PDNS_PTR_AFTER=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$FULL_REV_NAME" PTR +short)
if [ -n "$PDNS_PTR_AFTER" ]; then
    log_fail "Stale IPv6 PTR record still present in PowerDNS after deletion: $PDNS_PTR_AFTER"
fi
log_pass "IPv6 PTR record cleanly purged from PowerDNS."

step "Cleanup"
delete_zone_all_views ipv6-test.com
delete_zone_all_views f.e.e.b.8.b.d.0.1.0.0.2.ip6.arpa

log_pass "Test ipam-ipv6-nibble-lifecycle completed successfully."
