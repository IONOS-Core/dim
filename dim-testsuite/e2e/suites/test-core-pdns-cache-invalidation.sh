#!/bin/bash
#
# suites/test-core-pdns-cache-invalidation.sh
# End-to-end test verifying PowerDNS cache-invalidation architecture guarantees.
#
# Threat Model (The Silent Cache Desync):
# The pdns-output daemon synchronizes DNS mutations by executing direct SQL statements
# (INSERT/UPDATE/DELETE) against the backend MariaDB database (pdns.records).
# pdns-output has no integration with pdns_control or the PowerDNS REST API to flush caches.
#
# Consequently, if PowerDNS authoritative servers are configured with packet or query caching
# (cache-ttl > 0 or query-cache-ttl > 0), PowerDNS continues serving stale answers from RAM
# after an update in DIM, ignoring urgent emergency changes or failovers.
#
# Architectural Invariant:
# To guarantee immediate consistency without cache desync, PowerDNS backend configuration
# in pdns-unit.conf, pdns1.conf, and pdns2.conf MUST strictly enforce zero-caching:
#   cache-ttl=0
#   query-cache-ttl=0
#   negquery-cache-ttl=0
#   zone-cache-refresh-interval=0
#   domain-metadata-cache-ttl=0
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="cache-inval-eval.com"

cleanup_cache_test() {
    delete_zone_all_views "$ZONE"
}
trap cleanup_cache_test EXIT INT TERM

step "Clean up previous runs"
cleanup_cache_test

step "Test 1: Verify PowerDNS configuration enforces zero-cache invariant"
# Inspect active PowerDNS configuration files to verify caching is disabled
for conf in /src/dim-testsuite/docker/pdns1.conf /src/dim-testsuite/docker/pdns2.conf /src/dim-testsuite/docker/pdns-unit.conf; do
    if [ -f "$conf" ]; then
        for param in cache-ttl query-cache-ttl negquery-cache-ttl zone-cache-refresh-interval domain-metadata-cache-ttl; do
            VAL=$(grep "^${param}=" "$conf" | cut -d'=' -f2 || true)
            if [ "$VAL" != "0" ]; then
                log_fail "PowerDNS config $conf violates zero-cache invariant: $param=$VAL (must be 0 to prevent silent desync)!"
            fi
        done
        log_pass "Verified zero-cache settings in $(basename "$conf")."
    fi
done

step "Test 2: Verify immediate end-to-end propagation without stale caching"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

ndcli --quiet create rr "app.${ZONE}." A 192.0.2.1

# Wait for initial record
SYNCED=false
for _ in $(seq 1 20); do
    IP1=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "app.${ZONE}." A +short)
    if [ "$IP1" = "192.0.2.1" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "Initial sync flag set" "true" "$SYNCED"
assert_equals "Initial record served by PowerDNS" "192.0.2.1" "$IP1"

# Perform immediate update
ndcli --quiet create rr "app.${ZONE}." A 198.51.100.1 --overwrite-a

# Verify immediate visibility in PowerDNS
UPDATED=false
for _ in $(seq 1 20); do
    IP2=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "app.${ZONE}." A +short)
    if [ "$IP2" = "198.51.100.1" ]; then
        UPDATED=true
        break
    fi
    sleep 0.5
done
assert_equals "Update sync flag set" "true" "$UPDATED"
assert_equals "Updated record immediately visible without stale cache delay" "198.51.100.1" "$IP2"

step "Cleanup"
cleanup_cache_test

log_pass "PowerDNS cache invalidation and zero-cache invariant verified."
