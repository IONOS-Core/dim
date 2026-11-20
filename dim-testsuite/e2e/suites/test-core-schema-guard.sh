#!/bin/bash
#
# suites/test-core-schema-guard.sh
# E2E Test: Database schema version checking, startup prevention on schema mismatch, and manage_db upgrade execution
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

MANAGE_DB="/src/dim-testsuite/virtual_env/bin/manage_db"
ROLLBACK_SQL="/src/dim/dim/sql/rollback_13_to_12.sql"
EXPECTED_VERSION=13

cleanup_schema() {
    # Best-effort trap restore: ensure database is restored to version 13 even if tests failed midway
    sql_command "UPDATE schemainfo SET version=${EXPECTED_VERSION} WHERE id=1;" 2>/dev/null || true
    # Restore is_pseudo column if dropped by rollback script
    sql_command "ALTER TABLE \`user\` ADD COLUMN \`is_pseudo\` tinyint(1) DEFAULT NULL;" 2>/dev/null || true
}
trap cleanup_schema EXIT INT TERM

step "Clean up and ensure baseline schema version ($EXPECTED_VERSION)"
cleanup_schema

step "Test 1: Normal state - manage_db check returns OK"
CHECK_INIT=$($MANAGE_DB check 2>&1)
echo "  manage_db check output: $CHECK_INIT"
assert_contains "Baseline schema version is OK" "OK" "$CHECK_INIT"

step "Test 2: Simulate schema rollback (version 13 -> 12)"
$MANAGE_DB script "$ROLLBACK_SQL"

DB_VERSION_AFTER=$(sql_command "SELECT version FROM schemainfo WHERE id=1;")
assert_equals "Database schema rolled back to version 12" "12" "$DB_VERSION_AFTER" "Schema Version"

step "Test 3: Schema Mismatch Detection - manage_db check warns and non-CLI startup is blocked"
set +e
MISMATCH_OUT=$($MANAGE_DB check 2>&1)
set -e

echo "  Mismatch check output:"
echo "$MISMATCH_OUT"

assert_contains "Critical mismatch log emitted" "DATABASE SCHEMA MISMATCH" "$MISMATCH_OUT"
assert_contains "Mismatch notice in check output" "has wrong schema version" "$MISMATCH_OUT"

# Verify web application refuses to start (exit code 1) on schema mismatch
set +e
APP_START_OUT=$(python3 -c "import dim, os; os.environ['DIM_CONFIG']='/etc/dim/dim.cfg'; dim.create_app()" 2>&1)
EXIT_APP_START=$?
set -e

assert_exit_code "Web application startup blocked on schema mismatch" 1 "$EXIT_APP_START" "$APP_START_OUT"
assert_contains "Startup blocked log" "Please run 'manage_db upgrade' before starting" "$APP_START_OUT"

step "Test 4: manage_db upgrade can still execute despite schema mismatch"
set +e
UPGRADE_OUT=$($MANAGE_DB upgrade 2>&1)
EXIT_UPGRADE=$?
set -e

echo "  manage_db upgrade output:"
echo "$UPGRADE_OUT"
assert_exit_code "manage_db upgrade succeeds" 0 "$EXIT_UPGRADE" "$UPGRADE_OUT"

step "Test 5: Assert schema version restored to expected version ($EXPECTED_VERSION)"
RESTORED_VERSION=$(sql_command "SELECT version FROM schemainfo WHERE id=1;")
assert_equals "Database schema upgraded back to target version" "$EXPECTED_VERSION" "$RESTORED_VERSION" "Schema Version"

FINAL_CHECK=$($MANAGE_DB check 2>&1)
assert_contains "Final schema check returns OK" "OK" "$FINAL_CHECK"

step "Cleanup"
cleanup_schema

log_pass "Database schema mismatch guard and upgrade lifecycle test passed."
