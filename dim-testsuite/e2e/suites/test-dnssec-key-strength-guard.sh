#!/bin/bash
#
# suites/test-dnssec-key-strength-guard.sh
# End-to-end test validating DNSSEC cryptographic key strength constraints (RFC 8624).
#
# Threat Model:
# RFC 8624 Section 3.1 mandates cryptographic key size requirements:
#   For Algorithm 8 (RSASHA256), Key Signing Keys (KSK) MUST NOT be less than 2048 bits.
#
# Validating resolvers (BIND, Unbound, Google 8.8.8.8) enforce cryptographic security policies
# and reject zones signed with sub-2048-bit RSA KSKs as insecure/BOGUS, triggering SERVFAIL outages.
#
# Expected Safety Guard:
# DIM must reject enabling DNSSEC with RSA KSK < 2048 bits with a clean validation error:
# 'Invalid key length: RSA KSK must be >= 2048 bits (RFC 8624)'.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="weak-ksk-eval.com"

cleanup_weak_ksk() {
    delete_zone_all_views "$ZONE"
}
trap cleanup_weak_ksk EXIT INT TERM

step "Clean up previous runs"
cleanup_weak_ksk

step "Create zone: ${ZONE}"
ndcli --quiet create zone "$ZONE"

step "Test 1: Guard - 512-bit RSA key is rejected"
set +e
OUT_512=$(ndcli modify zone "$ZONE" dnssec enable 8 ksk 512 zsk 512 2>&1)
EXIT_512=$?
set -e
assert_exit_code "512-bit RSA key rejected" 1 "$EXIT_512" "$OUT_512"
assert_contains "512-bit error message" "RSA modulus length must be >= 1024" "$OUT_512"

step "Test 2: RFC 8624 Compliance - Verify whether 1024-bit KSK is strictly rejected"
set +e
OUT_1024=$(ndcli modify zone "$ZONE" dnssec enable 8 ksk 1024 zsk 1024 2>&1)
EXIT_1024=$?
set -e

echo "  DIM response for 1024-bit KSK creation:"
echo "  $OUT_1024"

# RFC 8624 Safety Guard Assertion:
# DIM SHOULD reject Algorithm 8 with KSK < 2048 to prevent resolver SERVFAIL!
if [ "$EXIT_1024" -ne 0 ]; then
    log_pass "DIM successfully enforced RFC 8624: 1024-bit RSA KSK rejected."
else
    log_fail "DEFECT DEMONSTRATED: DIM permitted 1024-bit RSA KSK, violating RFC 8624 and causing resolver SERVFAIL rejection!"
fi

step "Cleanup"
cleanup_weak_ksk
