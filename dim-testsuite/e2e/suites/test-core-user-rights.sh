#!/bin/bash
#
# suites/test-core-user-rights.sh
# End-to-end test verifying RBAC user rights, group isolation, and subzone permission inheritance.
# Proves that:
#   1. Unprivileged users cannot create zones or touch unauthorized resources.
#   2. Granting zone-scoped rights (create_rr, delete_rr) to a user-group authorizes its members.
#   3. Authorized users remain strictly isolated from other zones (no cross-tenant escalation).
#   4. Subzone creation inherits rights from parent zones by default.
#   5. Subzone creation with --no-inherit-rights prevents permission leakage.
#   6. Revoking rights immediately closes access.
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

USER="john1"
USER_PW="P@ssw0rd"
GROUP="team-alpha"
ADMIN_ZONE="sec-admin-only.com"
TEAM_ZONE="team-alpha-shared.com"
SUB_INHERIT="sub.${TEAM_ZONE}"
SUB_RESTRICT="restricted.${TEAM_ZONE}"

# Helper to run ndcli as john1
ndcli_john() {
    ndcli -u "$USER" -p "$USER_PW" "$@"
}

step "Clean up previous runs"
delete_zone_all_views "$SUB_RESTRICT"
delete_zone_all_views "$SUB_INHERIT"
delete_zone_all_views "$TEAM_ZONE"
delete_zone_all_views "$ADMIN_ZONE"
ndcli delete user-group "$GROUP" -q 2>/dev/null || true

step "Test 1: Unprivileged user denied global zone creation"
if ndcli_john create zone "unauthorized.com" 2>/dev/null; then
    log_fail "Unprivileged user created a zone without permissions!"
else
    log_pass "Unprivileged user correctly denied zone creation (can_create_forward_zones)."
fi

step "Create admin and team zones as admin"
ndcli create zone "$ADMIN_ZONE" -q
ndcli create zone "$TEAM_ZONE" -q

step "Test 2: Unprivileged user denied modifying admin zone"
if ndcli_john create rr "hack.${ADMIN_ZONE}." A 10.1.1.1 2>/dev/null; then
    log_fail "Unprivileged user was able to inject RR into admin zone!"
else
    log_pass "Unprivileged user correctly denied modifying admin zone."
fi

step "Test 3: Setup user group and grant zone-scoped RR rights"
ndcli create user-group "$GROUP" -q
ndcli modify user-group "$GROUP" add user "$USER" -q
ndcli modify user-group "$GROUP" grant create_rr "$TEAM_ZONE" -q
ndcli modify user-group "$GROUP" grant delete_rr "$TEAM_ZONE" -q

# Verify user can now create RR in authorized zone
ndcli_john create rr "app.${TEAM_ZONE}." A 10.20.1.1 -q
log_pass "Authorized user successfully created RR in permitted zone."

# Verify user CANNOT create RR in unauthorized admin zone
if ndcli_john create rr "exploit.${ADMIN_ZONE}." A 10.20.1.1 2>/dev/null; then
    log_fail "Authorized user breached cross-tenant boundary into admin zone!"
else
    log_pass "Authorized user strictly blocked from unauthorized admin zone."
fi

# Verify user CANNOT delete the zone itself (requires zone_admin)
if ndcli_john delete zone "$TEAM_ZONE" --cleanup 2>/dev/null; then
    log_fail "User with create_rr/delete_rr was able to delete the whole zone!"
else
    log_pass "User correctly denied zone deletion (requires zone_admin/dns_admin)."
fi

step "Test 4: Verify default subzone rights inheritance"
# Admin creates subzone without flags -> team-alpha should inherit rights
ndcli create zone "$SUB_INHERIT" -q

# User should be able to create RR in inherited subzone
ndcli_john create rr "api.${SUB_INHERIT}." A 10.20.1.2 -q
log_pass "User inherited create_rr rights in child subzone by default."

step "Test 5: Verify subzone creation with --no-inherit-rights prevents access leakage"
# Admin creates restricted subzone with --no-inherit-rights
ndcli create zone "$SUB_RESTRICT" --no-inherit-rights -q

# User should be BLOCKED from restricted subzone
if ndcli_john create rr "api.${SUB_RESTRICT}." A 10.20.1.3 2>/dev/null; then
    log_fail "User gained access to subzone created with --no-inherit-rights!"
else
    log_pass "User correctly denied access to subzone with --no-inherit-rights."
fi

step "Test 6: Revoke rights and verify immediate access termination"
ndcli modify user-group "$GROUP" revoke create_rr "$TEAM_ZONE" -q

if ndcli_john create rr "blocked.${TEAM_ZONE}." A 10.20.1.99 2>/dev/null; then
    log_fail "User was still able to create RR after right revocation!"
else
    log_pass "Right revocation immediately terminated user write access."
fi

step "Cleanup"
delete_zone_all_views "$SUB_RESTRICT"
delete_zone_all_views "$SUB_INHERIT"
delete_zone_all_views "$TEAM_ZONE"
delete_zone_all_views "$ADMIN_ZONE"
ndcli delete user-group "$GROUP" -q

log_pass "Test core-user-rights completed successfully."
