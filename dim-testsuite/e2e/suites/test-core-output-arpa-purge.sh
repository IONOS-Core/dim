#!/bin/bash
#
# suites/test-core-output-arpa-purge.sh
# E2E Test: Authoritative Reverse in-addr.arpa zone detachment, complete PTR/NS purge, and re-attachment sync
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_arpa_purge() {
    delete_zone_all_views 218.168.192.in-addr.arpa
    delete_zone_all_views arpa-fwd.com
    ndcli -q modify output out remove zone-group arpa-purge-zg 2>/dev/null || true
    ndcli -q delete zone-group arpa-purge-zg 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_arpa_purge

step "Setup dedicated zone-group, attach to output, and create reverse zone with PTR and NS delegation"
ndcli --quiet create zone-group arpa-purge-zg
ndcli --quiet modify output out add zone-group arpa-purge-zg

ndcli --quiet create zone 218.168.192.in-addr.arpa
ndcli --quiet modify zone-group arpa-purge-zg add zone 218.168.192.in-addr.arpa

ndcli --quiet create zone arpa-fwd.com
ndcli --quiet create rr host1.arpa-fwd.com. A 192.168.218.10
ndcli --quiet modify zone 218.168.192.in-addr.arpa create rr 128/26 NS ns1.partner.example.org.

step "Wait for PowerDNS to receive initial reverse zone with PTR and NS delegation"
SYNCED=false
PDNS_PTR=""
PDNS_NS=""
for _ in $(seq 1 20); do
    PDNS_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.218.168.192.in-addr.arpa. PTR +short)
    PDNS_NS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 128/26.218.168.192.in-addr.arpa. NS +noall +authority | awk '{print $5}')
    if [ "$PDNS_PTR" = host1.arpa-fwd.com. ] && [ "$PDNS_NS" = ns1.partner.example.org. ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received reverse PTR and NS delegation" "true" "$SYNCED"

step "Test 1: Detach reverse zone from zone-group and assert complete purge in PowerDNS"
ndcli --quiet modify zone-group arpa-purge-zg remove zone 218.168.192.in-addr.arpa

# Wait for PowerDNS purge
PURGED=false
PDNS_PTR_DETACH=""
PDNS_NS_DETACH=""
for _ in $(seq 1 20); do
    PDNS_PTR_DETACH=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.218.168.192.in-addr.arpa. PTR +short)
    PDNS_NS_DETACH=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 128/26.218.168.192.in-addr.arpa. NS +noall +authority)
    if [ -z "$PDNS_PTR_DETACH" ] && [ -z "$PDNS_NS_DETACH" ]; then
        PURGED=true
        break
    fi
    sleep 1
done

assert_equals "Reverse zone completely purged from PowerDNS (no zombie PTRs)" "true" "$PURGED"

step "Test 2: Re-attach reverse zone and verify complete restoration in PowerDNS"
ndcli --quiet modify zone-group arpa-purge-zg add zone 218.168.192.in-addr.arpa

RESTORED=false
PDNS_PTR_RECON=""
PDNS_NS_RECON=""
for _ in $(seq 1 20); do
    PDNS_PTR_RECON=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 10.218.168.192.in-addr.arpa. PTR +short)
    PDNS_NS_RECON=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" 128/26.218.168.192.in-addr.arpa. NS +noall +authority | awk '{print $5}')
    if [ "$PDNS_PTR_RECON" = host1.arpa-fwd.com. ] && [ "$PDNS_NS_RECON" = ns1.partner.example.org. ]; then
        RESTORED=true
        break
    fi
    sleep 1
done

assert_equals "Reverse PTR and NS delegation restored upon re-attachment" "true" "$RESTORED"

step "Cleanup"
cleanup_arpa_purge

log_pass "Reverse in-addr.arpa output detachment and restoration test passed."
