#!/bin/bash
#
# suites/test-core-rights-attribute-revoke.sh
# End-to-end test validating attribute access right revocation integrity on pools.
#
# Threat Model (The Silent Revocation Failure):
# Administrators grant attribute rights to user groups on pools (e.g. 'audit.' prefix).
# When revoking the attribute permission (e.g. running 'revoke attr audit' instead of 'audit.'
# or vice versa), DIM silently succeeds with exit code 0 without any warning.
#
# However, the access right is NOT revoked in the database and remains fully granted to the group.
# This represents a critical permission leak where revoked groups retain unauthorized attribute rights.
#
# Expected Behavior:
# Revoking attribute access must either normalize trailing dots or strictly validate matching
# rights, and under no circumstances may a revoke command succeed with exit code 0 while
# leaving the granted permission active on the object.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

GROUP_REVOKE="sec-audit-team"
POOL_REVOKE="finance-data-pool"

cleanup_attr_revoke() {
    # Best-effort cleanup of test artifacts
    ndcli -q delete pool "$POOL_REVOKE" 2>/dev/null || true
    ndcli -q delete user-group "$GROUP_REVOKE" 2>/dev/null || true
}
trap cleanup_attr_revoke EXIT INT TERM

step "Clean up previous runs"
cleanup_attr_revoke

step "Setup user group and pool"
ndcli --quiet create user-group "$GROUP_REVOKE"
ndcli --quiet create pool "$POOL_REVOKE"

step "Test 1: Grant attribute permission with trailing dot (audit.)"
ndcli --quiet modify user-group "$GROUP_REVOKE" grant attr "audit." "$POOL_REVOKE"

ALL_RIGHTS=$(ndcli list pool "$POOL_REVOKE" rights -H)
INITIAL_RIGHTS=$(echo "$ALL_RIGHTS" | grep "$GROUP_REVOKE" || test $? -eq 1)
echo "  Granted rights on pool: $INITIAL_RIGHTS"
assert_contains "Attribute right granted" "attr.audit." "$INITIAL_RIGHTS"

step "Test 2: Revoke attribute permission without trailing dot (audit)"
REVOKE_OUT=$(ndcli modify user-group "$GROUP_REVOKE" revoke attr "audit" "$POOL_REVOKE" 2>&1)
REVOKE_EXIT=$?

echo "  Revoke command output (exit $REVOKE_EXIT):"
echo "  $REVOKE_OUT"

step "Test 3: Assert permission was actually revoked from the pool"
REMAINING_RIGHTS=$(ndcli list pool "$POOL_REVOKE" rights -H)
POST_RIGHTS=$(echo "$REMAINING_RIGHTS" | grep "$GROUP_REVOKE" || test $? -eq 1)
echo "  Remaining rights on pool after revoke: $POST_RIGHTS"

# Security Assertion: The right MUST NOT linger after a successful revoke command!
if echo "$POST_RIGHTS" | grep -q "attr.audit."; then
    log_fail "DEFECT DEMONSTRATED: Revoke command exited 0, but permission 'attr.audit.' was NOT revoked from pool!"
else
    log_pass "Attribute permission successfully revoked."
fi

step "Cleanup"
cleanup_attr_revoke
