#!/bin/bash
#
# tests/e2e/test-lib.sh - Shared functions for DIM E2E tests
#

set -e

# Terminal color palette
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    C_GREEN="\033[1;32m"
    C_RED="\033[1;31m"
    C_YELLOW="\033[1;33m"
    C_CYAN="\033[1;36m"
    C_BOLD="\033[1m"
    C_RESET="\033[0m"
else
    C_GREEN=""
    C_RED=""
    C_YELLOW=""
    C_CYAN=""
    C_BOLD=""
    C_RESET=""
fi

log_pass() { echo -e "  ${C_GREEN}[PASS]${C_RESET} $1"; }
log_fail() { echo -e "  ${C_RED}[FAIL]${C_RESET} $1" >&2; exit 1; }
log_warn() { echo -e "  ${C_YELLOW}[WARN]${C_RESET} $1"; }
step()     { echo -e "\n${C_CYAN}${C_BOLD}==> $1${C_RESET}"; }

# --- Readable Assertion Helpers (dhcpt style, typed English output) ---
assert_equals() {
    local label="$1"
    local expected="$2"
    local actual="$3"
    local type_tag="${4:-Value}"

    if [ "$actual" = "$expected" ]; then
        log_pass "$label: $actual"
        return 0
    fi

    echo -e "  ${C_RED}[FAIL] Assertion Failed: $label${C_RESET}" >&2
    echo -e "         ${C_CYAN}Expected [${type_tag}] :${C_RESET} $expected" >&2
    echo -e "         ${C_YELLOW}Received [${type_tag}] :${C_RESET} ${actual:-<empty>}" >&2
    exit 1
}

assert_contains() {
    local label="$1"
    local needle="$2"
    local haystack="$3"

    if echo "$haystack" | grep --quiet --fixed-strings "$needle"; then
        log_pass "$label contains '$needle'"
        return 0
    fi

    echo -e "  ${C_RED}[FAIL] Assertion Failed: $label${C_RESET}" >&2
    echo -e "         ${C_CYAN}Expected [To Contain] :${C_RESET} $needle" >&2
    echo -e "         ${C_YELLOW}Received [Output Snippet] :${C_RESET}" >&2
    echo "$haystack" | head -n 5 | sed 's/^/           /' >&2
    exit 1
}

assert_exit_code() {
    local label="$1"
    local expected_code="$2"
    local actual_code="$3"
    local output="${4:-}"

    if [ "$actual_code" -eq "$expected_code" ]; then
        log_pass "$label (exit code $actual_code)"
        return 0
    fi

    echo -e "  ${C_RED}[FAIL] Exit Code Assertion Failed: $label${C_RESET}" >&2
    echo -e "         ${C_CYAN}Expected [Exit Code] :${C_RESET} $expected_code" >&2
    echo -e "         ${C_YELLOW}Received [Exit Code] :${C_RESET} $actual_code" >&2
    if [ -n "$output" ]; then
        echo -e "         ${C_YELLOW}Received [Output Snippet] :${C_RESET} $(echo "$output" | head -n 1)" >&2
    fi
    exit 1
}

assert_not_empty() {
    local label="$1"
    local actual="$2"
    local type_tag="${3:-Value}"

    if [ -n "$actual" ]; then
        log_pass "$label is not empty"
        return 0
    fi

    echo -e "  ${C_RED}[FAIL] Assertion Failed: $label${C_RESET}" >&2
    echo -e "         ${C_CYAN}Expected [${type_tag}] :${C_RESET} <non-empty>" >&2
    echo -e "         ${C_YELLOW}Received [${type_tag}] :${C_RESET} <empty>" >&2
    exit 1
}

sql_command() {
    local query=$1
    ${MYSQL_CMD:-mysql -h127.0.0.1 -P3307 -udim -pdim -N dim} -e "${query}"
}

pdns_sql() {
    local query=$1
    ${PDNS_MYSQL_CMD:-mysql -h127.0.0.1 -P3307 -updns -ppdns -N pdns1} -e "${query}"
}

delete_zone_all_views() {
    local zone=$1
    # Check if zone exists
    if ndcli list zone "$zone" views >/dev/null 2>&1; then
        # Delete extra views first (skip default/last view)
        for view in $(ndcli list zone "$zone" views -H 2>/dev/null || true); do
            ndcli modify zone "$zone" delete view "$view" --cleanup -q 2>/dev/null || true
        done
        ndcli delete zone "$zone" --cleanup -q 2>/dev/null || true
    fi
}

ensure_output_sync() {
    local zone=$1
    if ! ndcli show zone-group zg >/dev/null 2>&1; then
        ndcli create zone-group zg -q >/dev/null 2>&1 || true
    fi
    if ! ndcli show output out 2>/dev/null | grep -q zg; then
        ndcli modify output out add zone-group zg -q >/dev/null 2>&1 || true
    fi
    ndcli modify zone-group zg add zone "$zone" -q >/dev/null 2>&1 || true
}

setup_env() {
    export PATH=/src/dim-testsuite/virtual_env/bin:$PATH
    export NDCLI_SERVER=${NDCLI_SERVER:-http://localhost:5000}
    export NDCLI_USERNAME=${NDCLI_USERNAME:-admin}
    export NDCLI_PASSWORD=${NDCLI_PASSWORD:-p}
    export DIM_CONFIG=${DIM_CONFIG:-/etc/dim/dim.cfg}
}
