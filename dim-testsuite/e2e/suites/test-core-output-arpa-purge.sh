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

REV_ZONE="218.168.192.in-addr.arpa"
FWD_ZONE="arpa-fwd.com"
ZG_NAME="arpa-purge-zg"
PTR_QUERY="10.${REV_ZONE}."
DELEG_QUERY="128/26.${REV_ZONE}."
HOST_TARGET="host1.${FWD_ZONE}."
NS_TARGET="ns1.partner.example.org."

cleanup_arpa_purge() {
    delete_zone_all_views "$REV_ZONE"
    delete_zone_all_views "$FWD_ZONE"
    ndcli -q modify output out remove zone-group "$ZG_NAME" 2>/dev/null || true
    ndcli -q delete zone-group "$ZG_NAME" 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_arpa_purge

step "Setup dedicated zone-group, attach to output, and create reverse zone with PTR and NS delegation"
ndcli --quiet create zone-group "$ZG_NAME"
ndcli --quiet modify output out add zone-group "$ZG_NAME"

ndcli --quiet create zone "$REV_ZONE"
ndcli --quiet modify zone-group "$ZG_NAME" add zone "$REV_ZONE"

ndcli --quiet create zone "$FWD_ZONE"
ndcli --quiet create rr "$HOST_TARGET" A 192.168.218.10
ndcli --quiet modify zone "$REV_ZONE" create rr 128/26 NS "$NS_TARGET"

step "Wait for PowerDNS to receive initial reverse zone with PTR and NS delegation"
SYNCED=false
PDNS_PTR=""
PDNS_NS=""
for _ in $(seq 1 20); do
    PDNS_PTR=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_QUERY" PTR +short)
    PDNS_NS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$DELEG_QUERY" NS +noall +authority | awk '{print $5}')
    if [ "$PDNS_PTR" = "$HOST_TARGET" ] && [ "$PDNS_NS" = "$NS_TARGET" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS received reverse PTR and NS delegation" "true" "$SYNCED"

step "Test 1: Detach reverse zone from zone-group and assert complete purge in PowerDNS"
ndcli --quiet modify zone-group "$ZG_NAME" remove zone "$REV_ZONE"

# Wait for PowerDNS purge
PURGED=false
PDNS_PTR_DETACH=""
PDNS_NS_DETACH=""
for _ in $(seq 1 20); do
    PDNS_PTR_DETACH=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_QUERY" PTR +short)
    PDNS_NS_DETACH=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$DELEG_QUERY" NS +noall +authority)
    if [ -z "$PDNS_PTR_DETACH" ] && [ -z "$PDNS_NS_DETACH" ]; then
        PURGED=true
        break
    fi
    sleep 1
done

assert_equals "Reverse zone completely purged from PowerDNS (no zombie PTRs)" "true" "$PURGED"

step "Test 2: Re-attach reverse zone and verify complete restoration in PowerDNS"
ndcli --quiet modify zone-group "$ZG_NAME" add zone "$REV_ZONE"

RESTORED=false
PDNS_PTR_RECON=""
PDNS_NS_RECON=""
for _ in $(seq 1 20); do
    PDNS_PTR_RECON=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$PTR_QUERY" PTR +short)
    PDNS_NS_RECON=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "$DELEG_QUERY" NS +noall +authority | awk '{print $5}')
    if [ "$PDNS_PTR_RECON" = "$HOST_TARGET" ] && [ "$PDNS_NS_RECON" = "$NS_TARGET" ]; then
        RESTORED=true
        break
    fi
    sleep 1
done

assert_equals "Reverse PTR and NS delegation restored upon re-attachment" "true" "$RESTORED"

step "Cleanup"
cleanup_arpa_purge

log_pass "Reverse in-addr.arpa output detachment and restoration test passed."
