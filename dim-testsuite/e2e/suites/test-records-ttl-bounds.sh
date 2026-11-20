#!/bin/bash
#
# suites/test-records-ttl-bounds.sh
# E2E Test: RFC 2181 TTL boundary enforcement (0 <= TTL <= 2147483647) and negative TTL rejection
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

ZONE="ttl-bounds-eval.com"
MAX_RFC2181_TTL=2147483647

cleanup_ttl() {
    delete_zone_all_views "$ZONE"
}

step "Clean up previous runs"
cleanup_ttl

step "Create forward zone"
ndcli --quiet create zone "$ZONE"
ensure_output_sync "$ZONE"

step "Test 1: Valid boundary TTLs (0 and max 2147483647) succeed cleanly"
ndcli --quiet create rr "zero.${ZONE}." ttl 0 A 10.1.1.1 --only-forward
ndcli --quiet create rr "max.${ZONE}." ttl "$MAX_RFC2181_TTL" A 10.1.1.2 --only-forward

TTL_ZERO=$(ndcli list rrs "zero.${ZONE}." A -H | awk '{print $4}')
assert_equals "TTL 0 accepted" "0" "$TTL_ZERO" "TTL"

TTL_MAX=$(ndcli list rrs "max.${ZONE}." A -H | awk '{print $4}')
assert_equals "Max RFC 2181 TTL (2147483647) accepted" "$MAX_RFC2181_TTL" "$TTL_MAX" "TTL"

step "Test 2: Guard - TTL exceeding 2147483647 (> 2^31 - 1) must be rejected with clean validation error"
# In DNS RFC 2181, TTL cannot exceed 2147483647. Values above 2^31-1 overflow Java signed 32-bit int to negative
# and crash pdns-output with InvalidTTLException: -1.
# DIM must reject it cleanly at API layer instead of crashing MySQL with unhandled DataError.
set +e
OVERFLOW_TTL_OUT=$(ndcli create rr "overflow.${ZONE}." ttl 3000000000 A 10.1.1.3 --only-forward 2>&1)
EXIT_OVERFLOW=$?
set -e

echo "  TTL overflow output: $OVERFLOW_TTL_OUT"
assert_exit_code "TTL exceeding 2147483647 rejected" 1 "$EXIT_OVERFLOW" "$OVERFLOW_TTL_OUT"
assert_contains "Clean TTL validation error message" "Invalid TTL" "$OVERFLOW_TTL_OUT"

step "Test 3: Guard - Negative TTL (-1) must be rejected"
# Negative TTL must be rejected to prevent pdns-output / dnsjava InvalidTTLException: -1 queue stalls.
set +e
NEG_TTL_OUT=$(/src/dim-testsuite/virtual_env/bin/python3 -c "
import dim, os
os.environ['DIM_CONFIG'] = '/etc/dim/dim.cfg'
app = dim.create_app()
with app.app_context():
    z = dim.models.Zone.query.filter_by(name='${ZONE}').first()
    v = z.views[0]
    dim.models.RR(name='neg.${ZONE}.', view=v, type='A', ttl=-1, value='1.2.3.4')
" 2>&1)
EXIT_NEG=$?
set -e

echo "  Negative TTL output: $NEG_TTL_OUT"
assert_exit_code "Negative TTL rejected by model validator" 1 "$EXIT_NEG" "$NEG_TTL_OUT"

step "Cleanup"
cleanup_ttl

log_pass "RFC 2181 TTL boundary enforcement test passed."
