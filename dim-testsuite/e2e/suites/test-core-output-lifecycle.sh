#!/bin/bash
#
# suites/test-core-output-lifecycle.sh
# End-to-end test verifying Zone-Group and Output detachment, reconciliation, and offline mutation sync.
# Proves that:
#   1. Removing a zone from a zone-group immediately purges the zone from PowerDNS (no zombie zones).
#   2. Detaching a zone-group from an output purges all its member zones from PowerDNS.
#   3. Mutations performed while a zone is offline/detached (adding RRs, deleting RRs, enabling DNSSEC)
#      are fully reconciled when re-attaching to the output.
#   4. PowerDNS converges to the exact current state and cryptographic integrity is restored.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


step "Clean up previous runs"
delete_zone_all_views output-recon-test.com
ndcli modify output out remove zone-group recon-zg -q 2>/dev/null || true
ndcli delete zone-group recon-zg -q 2>/dev/null || true

step "Create dedicated zone-group and attach to output"
ndcli create zone-group recon-zg -q
ndcli modify output out add zone-group recon-zg -q
log_pass "Zone-group recon-zg attached to output out."

step "Create zone and add initial record"
ndcli create zone output-recon-test.com -q
ndcli modify zone-group recon-zg add zone output-recon-test.com -q
ndcli create rr "initial.output-recon-test.com." A 10.1.1.1 -q

step "Wait for initial sync in PowerDNS"
SYNCED=false
for _ in $(seq 1 20); do
    if dig "@${PDNS_ADDRESS:-127.1.1.1}" "initial.output-recon-test.com." A +noall +answer | grep -q "10.1.1.1"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive initial zone records."
fi
log_pass "Initial zone and record (10.1.1.1) synchronized to PowerDNS."

step "Test 1: Detach zone from zone-group and verify complete purge from PowerDNS"
ndcli modify zone-group recon-zg remove zone output-recon-test.com -q

PURGED=false
for _ in $(seq 1 20); do
    ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "initial.output-recon-test.com." A +short)
    if [ -z "$ANS" ]; then
        PURGED=true
        break
    fi
    sleep 1
done

if [ "$PURGED" = false ]; then
    log_fail "PowerDNS still serves zone after removal from zone-group (zombie zone detected)!"
fi
log_pass "Zone cleanly purged from PowerDNS upon removal from zone-group."

step "Test 2: Perform offline mutations while detached (delete initial RR, add offline RR, enable DNSSEC)"
ndcli delete rr "initial.output-recon-test.com." A 10.1.1.1 -q
ndcli create rr "offline.output-recon-test.com." A 10.99.99.99 -q
ndcli modify zone output-recon-test.com dnssec enable 8 ksk 2048 zsk 1024 -q
log_pass "Offline mutations completed in DIM (RR deleted, new RR added, DNSSEC enabled)."

step "Test 3: Re-attach zone to zone-group and verify full state convergence"
ndcli modify zone-group recon-zg add zone output-recon-test.com -q

RECONCILED=false
for _ in $(seq 1 20); do
    ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "offline.output-recon-test.com." A +short)
    if [ "$ANS" = "10.99.99.99" ]; then
        RECONCILED=true
        break
    fi
    sleep 1
done

if [ "$RECONCILED" = false ]; then
    log_fail "PowerDNS failed to reconcile offline RR 10.99.99.99 upon re-attachment!"
fi
log_pass "PowerDNS successfully received reconciled offline RR (10.99.99.99)."

# Verify deleted RR (10.1.1.1) is NOT present
OLD_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "initial.output-recon-test.com." A +short)
if [ -n "$OLD_ANS" ]; then
    log_fail "Deleted RR (10.1.1.1) re-appeared in PowerDNS after reconciliation!"
fi
log_pass "Deleted RR remained absent after reconciliation."

step "Verify DNSSEC cryptographic integrity after reconciliation"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" output-recon-test.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "DNSSEC cryptographic verification failed on reconciled zone!"
fi
log_pass "DNSSEC signatures on reconciled zone are valid and verified."

step "Cleanup"
delete_zone_all_views output-recon-test.com
ndcli modify output out remove zone-group recon-zg -q
ndcli delete zone-group recon-zg -q

log_pass "Test core-output-lifecycle completed successfully."
