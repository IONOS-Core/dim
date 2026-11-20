#!/usr/bin/env bash
#
# dim-testsuite/e2e/run-all.sh - Extensible E2E test runner executing suites in suites/
#
# Usage:
#   ./run-all.sh                # Run all test suites
#   ./run-all.sh dnssec         # Run only suites matching *dnssec*
#   ./run-all.sh zone           # Run only suites matching *zone*
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITES_DIR="${SCRIPT_DIR}/suites"
FILTER="${1:-}"

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

PASSED_COUNT=0
FAILED_COUNT=0
FAILED_TESTS=()

echo ""
echo -e "${C_BOLD}======================================================================${C_RESET}"
if [ -n "$FILTER" ]; then
    echo -e "${C_BOLD}Executing DIM E2E test suites in ${SUITES_DIR} matching '${FILTER}'...${C_RESET}"
else
    echo -e "${C_BOLD}Discovering and executing all DIM E2E test suites in ${SUITES_DIR}...${C_RESET}"
fi
echo -e "${C_BOLD}======================================================================${C_RESET}"

# Find all executable test scripts in suites/
while IFS= read -r test_file; do
    [ -f "$test_file" ] && [ -x "$test_file" ] || continue

    rel_name="$(basename "$test_file")"
    if [ -n "$FILTER" ] && [[ "$rel_name" != *"$FILTER"* ]]; then
        continue
    fi

    echo ""
    echo -e "--- Running Suite: ${C_CYAN}${rel_name}${C_RESET} ---"

    test_output=""
    if test_output=$(bash "$test_file" 2>&1); then
        PASSED_COUNT=$((PASSED_COUNT + 1))
        echo -e "  ${C_GREEN}[PASS]${C_RESET} ${rel_name}"
    else
        FAILED_COUNT=$((FAILED_COUNT + 1))
        FAILED_TESTS+=("${rel_name}")
        echo -e "  ${C_RED}[FAIL]${C_RESET} ${rel_name}" >&2
        echo -e "${C_YELLOW}--- Failure Output for ${rel_name} ---${C_RESET}" >&2
        while IFS= read -r line; do
            echo "    $line" >&2
        done <<< "$test_output"
        echo -e "${C_YELLOW}--------------------------------------${C_RESET}" >&2
    fi
done < <(find "$SUITES_DIR" -maxdepth 1 -type f -name "test-*.sh" | sort)

echo ""
echo -e "${C_BOLD}======================================================================${C_RESET}"
if [ "${FAILED_COUNT}" -gt 0 ]; then
    echo -e "${C_RED}[FAIL]${C_RESET} E2E Test Summary: ${PASSED_COUNT} Passed, ${FAILED_COUNT} Failed"
    echo -e "${C_BOLD}======================================================================${C_RESET}"
    echo "Failed tests:" >&2
    for t in "${FAILED_TESTS[@]}"; do
        echo -e "  - ${C_RED}${t}${C_RESET}" >&2
    done
    exit 1
fi

echo -e "${C_GREEN}[OK]${C_RESET} E2E Test Summary: ${PASSED_COUNT} Passed, ${FAILED_COUNT} Failed"
echo -e "${C_BOLD}======================================================================${C_RESET}"
echo -e "${C_GREEN}[OK]${C_RESET} All ${PASSED_COUNT} E2E test suites passed successfully!"
exit 0
