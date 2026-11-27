#!/bin/bash
#
# suites/test-core-admin-commands.sh
# E2E Test: Server-side administration CLI commands on dim-mw (manage_db, manage_dim, and report)
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

MANAGE_DB="/src/dim-testsuite/virtual_env/bin/manage_db"
MANAGE_DIM="/src/dim-testsuite/virtual_env/bin/manage_dim"
REPORT_CMD="/src/dim-testsuite/virtual_env/bin/report"

cleanup_admin_cmds() {
    ndcli -q modify pool admin-eval-pool remove subnet 10.88.10.0/24 --force 2>/dev/null || true
    ndcli -q delete pool admin-eval-pool 2>/dev/null || true
    delete_zone_all_views "10.88.10.in-addr.arpa"
    ndcli -q delete container 10.88.0.0/16 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_admin_cmds

step "Test 1: manage_db check confirms database schema version and accessibility"
CHECK_OUT=$($MANAGE_DB check 2>&1)
echo "  manage_db check: $CHECK_OUT"
assert_contains "manage_db check returns OK" "OK" "$CHECK_OUT"

step "Test 2: manage_dim rebuild_tree executes successfully"
set +e
TREE_OUT=$($MANAGE_DIM rebuild_tree 2>&1)
EXIT_TREE=$?
set -e

echo "  manage_dim rebuild_tree output: $(echo "$TREE_OUT" | tr '\n' ' ' | head -c 80)..."
assert_exit_code "manage_dim rebuild_tree exit code" 0 "$EXIT_TREE" "$TREE_OUT"
assert_contains "rebuild_tree log completed" "for rebuild_tree()" "$TREE_OUT"

step "Test 3: report pool generates IPAM capacity report"
ndcli --quiet create container 10.88.0.0/16
ndcli --quiet create pool admin-eval-pool
ndcli --quiet modify pool admin-eval-pool add subnet 10.88.10.0/24

set +e
REPORT_OUT=$($REPORT_CMD pool admin-eval-pool 2>&1)
EXIT_REPORT=$?
set -e

echo "  report output:"
echo "$REPORT_OUT"

assert_exit_code "report pool exit code" 0 "$EXIT_REPORT" "$REPORT_OUT"
assert_contains "report header exists" "Pool admin-eval-pool" "$REPORT_OUT"
assert_contains "report calculates free IPs" "254 IPs are still free" "$REPORT_OUT"

step "Test 4: manage_dim autodns3 fails safely when registrar config is missing"
set +e
AUTODNS_OUT=$($MANAGE_DIM autodns3 2>&1)
EXIT_AUTODNS=$?
set -e

assert_exit_code "autodns3 without config exits with failure" 1 "$EXIT_AUTODNS" "$AUTODNS_OUT"
assert_contains "autodns3 reports missing config" "Error reading configuration file" "$AUTODNS_OUT"

step "Test 5: manage_dim set_user and delete_user manage administrative users"
sql_command "INSERT INTO user (username) VALUES ('testadminops');"

# Elevate user to Admin
SET_USER_OUT=$($MANAGE_DIM set_user -u testadminops -t Admin 2>&1)
echo "  manage_dim set_user output: $(echo "$SET_USER_OUT" | tr '\n' ' ' | head -c 80)..."
assert_contains "set_user log completed" "set_user" "$SET_USER_OUT"

# Delete user from DIM
DEL_USER_OUT=$($MANAGE_DIM delete_user -u testadminops 2>&1)
echo "  manage_dim delete_user output: $(echo "$DEL_USER_OUT" | tr '\n' ' ' | head -c 80)..."
assert_contains "delete_user log completed" "delete_user" "$DEL_USER_OUT"

USER_EXISTS=$(sql_command "SELECT COUNT(*) FROM user WHERE username='testadminops';")
assert_equals "User completely purged by delete_user" "0" "$USER_EXISTS" "User Count"

step "Cleanup"
cleanup_admin_cmds

log_pass "Server-side administration CLI commands test passed."
