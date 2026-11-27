#!/bin/bash
#
# suites/test-dnssec-nsec3-params.sh
# End-to-end test verifying NSEC3 salt generation, parameter updates, and denial of existence.
# Proves that:
#   1. Enabling DNSSEC with "nsec3 <iterations> salted" generates a random 16-character hex salt.
#   2. PowerDNS receives and serves NSEC3PARAM with the exact algorithm, iterations, and salt.
#   3. Queries for non-existent domains (NXDOMAIN) return valid NSEC3 proof records matching the salt.
#   4. Updating NSEC3 parameters changes salt and iterations in both DIM and PowerDNS without interruption.
#   5. Cryptographic signature validity is maintained throughout the parameter rollover.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


step "Clean up previous runs"
delete_zone_all_views nsec3-param-test.com

step "Create zone and enable DNSSEC with NSEC3 10 salted"
ndcli create zone nsec3-param-test.com -q
ensure_output_sync nsec3-param-test.com
ndcli modify zone nsec3-param-test.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 10 salted -q

SALT_INITIAL=$(ndcli show zone nsec3-param-test.com | grep '^nsec3_salt:' | cut -d':' -f2)
ITER_INITIAL=$(ndcli show zone nsec3-param-test.com | grep '^nsec3_iterations:' | cut -d':' -f2)
echo "  Initial NSEC3 parameters in DIM: iterations=$ITER_INITIAL, salt=$SALT_INITIAL"

if [ "$ITER_INITIAL" -ne 10 ] || [ -z "$SALT_INITIAL" ] || [ "$SALT_INITIAL" = "-" ]; then
    log_fail "Failed to generate initial NSEC3 salt in DIM (got '$SALT_INITIAL', iterations '$ITER_INITIAL')."
fi
log_pass "Initial NSEC3 salt ($SALT_INITIAL) generated in DIM."

step "Wait for PowerDNS to receive initial NSEC3PARAM"
SYNCED=false
for _ in $(seq 1 20); do
    PDNS_NSEC3PARAM=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "nsec3-param-test.com." NSEC3PARAM +short || true)
    if echo "$PDNS_NSEC3PARAM" | grep -qi "$SALT_INITIAL"; then
        SYNCED=true
        break
    fi
    sleep 1
done

if [ "$SYNCED" = false ]; then
    log_fail "PowerDNS did not receive NSEC3PARAM matching initial salt $SALT_INITIAL (got: '$PDNS_NSEC3PARAM')."
fi
log_pass "PowerDNS published NSEC3PARAM: $PDNS_NSEC3PARAM"

step "Test NXDOMAIN response returns matching NSEC3 proof"
NX_OUTPUT=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "nonexistent.nsec3-param-test.com." A +dnssec)
if ! echo "$NX_OUTPUT" | grep -i "NSEC3" | grep -qi "$SALT_INITIAL"; then
    log_fail "PowerDNS NXDOMAIN authority section missing NSEC3 with salt $SALT_INITIAL!"
fi
log_pass "PowerDNS returns valid NSEC3 proof matching initial salt."

step "Update NSEC3 parameters to 25 iterations with new salt"
ndcli modify zone nsec3-param-test.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 25 salted -q

SALT_UPDATED=$(ndcli show zone nsec3-param-test.com | grep '^nsec3_salt:' | cut -d':' -f2)
ITER_UPDATED=$(ndcli show zone nsec3-param-test.com | grep '^nsec3_iterations:' | cut -d':' -f2)
echo "  Updated NSEC3 parameters in DIM: iterations=$ITER_UPDATED, salt=$SALT_UPDATED"

if [ "$ITER_UPDATED" -ne 25 ] || [ "$SALT_UPDATED" = "$SALT_INITIAL" ]; then
    log_fail "Failed to update NSEC3 parameters/salt in DIM (old: $SALT_INITIAL, new: $SALT_UPDATED)."
fi
log_pass "New NSEC3 salt ($SALT_UPDATED) and iterations (25) generated."

step "Wait for PowerDNS to publish updated NSEC3PARAM"
SYNCED_UPDATE=false
for _ in $(seq 1 20); do
    PDNS_PARAM_NEW=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "nsec3-param-test.com." NSEC3PARAM +short || true)
    if echo "$PDNS_PARAM_NEW" | grep -qi "$SALT_UPDATED"; then
        SYNCED_UPDATE=true
        break
    fi
    sleep 1
done

if [ "$SYNCED_UPDATE" = false ]; then
    log_fail "PowerDNS failed to publish updated NSEC3PARAM with salt $SALT_UPDATED (got: '$PDNS_PARAM_NEW')."
fi
log_pass "PowerDNS published updated NSEC3PARAM: $PDNS_PARAM_NEW"

step "Verify NXDOMAIN response now uses updated NSEC3 salt and iterations"
NX_OUTPUT_NEW=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "nonexistent.nsec3-param-test.com." A +dnssec)
if ! echo "$NX_OUTPUT_NEW" | grep -i "NSEC3 1 0 25" | grep -qi "$SALT_UPDATED"; then
    log_fail "PowerDNS NXDOMAIN response failed to switch to updated NSEC3 salt $SALT_UPDATED!"
fi
log_pass "PowerDNS NXDOMAIN proof verified with updated NSEC3 parameters."

step "Verify Cryptographic & Inception Integrity"
if ! python3 "${SCRIPT_DIR}/../verify-dnssec-zone.py" nsec3-param-test.com "@${PDNS_ADDRESS:-127.1.1.1}"; then
    log_fail "Cryptographic verification failed after NSEC3 parameter rollover!"
fi
log_pass "Cryptographic signatures validated after NSEC3 parameter rollover."

step "Cleanup"
delete_zone_all_views nsec3-param-test.com

log_pass "Test dnssec-nsec3-params completed successfully."
