#!/usr/bin/env bash
#
# scripts/lint-diff.sh - Diff-aware linting for DIM
#
# Only checks lines and files modified in the current branch or staged commit.
# Supports:
#   - Shell: shellcheck (only modified .sh files)
#   - Python: darker (formatting check on diff) & ruff (linting modified files)
#   - Java: gradle compileJava (if any .java file in pdns-output is modified)
#

set -eo pipefail

MODE="working"
BASE_REF="HEAD"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --staged)
            MODE="staged"
            shift
            ;;
        --branch)
            MODE="branch"
            BASE_REF="$2"
            shift 2
            ;;
        *)
            echo "Usage: $0 [--staged | --branch <base-ref>]"
            exit 1
            ;;
    esac
done

# Color support
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

echo -e "${C_CYAN}${C_BOLD}==> Collecting modified files (${MODE} mode)...${C_RESET}"

CHANGED_FILES=()
if [ "$MODE" = "staged" ]; then
    while IFS= read -r file; do
        [ -n "$file" ] && CHANGED_FILES+=("$file")
    done < <(git diff --cached --name-only --diff-filter=d)
elif [ "$MODE" = "branch" ]; then
    while IFS= read -r file; do
        [ -n "$file" ] && CHANGED_FILES+=("$file")
    done < <(git diff --name-only "${BASE_REF}...HEAD" --diff-filter=d 2>/dev/null || git diff --name-only "${BASE_REF}" --diff-filter=d)
else
    # Default: staged, unstaged, and untracked files
    while IFS= read -r file; do
        [ -n "$file" ] && CHANGED_FILES+=("$file")
    done < <( { git diff --name-only HEAD --diff-filter=d 2>/dev/null || true; git status --porcelain | grep '^??' | awk '{print $2}'; } | sort -u )
fi

SH_FILES=()
PY_FILES=()
JAVA_FILES=()

for f in "${CHANGED_FILES[@]}"; do
    if [[ "$f" == *.sh ]] && [ -f "$f" ]; then
        SH_FILES+=("$f")
    elif [[ "$f" == *.py ]] && [ -f "$f" ]; then
        PY_FILES+=("$f")
    elif [[ "$f" == *.java ]] && [ -f "$f" ]; then
        JAVA_FILES+=("$f")
    fi
done

ERRORS=0

# 1. Shell Script Check (shellcheck)
if [ ${#SH_FILES[@]} -gt 0 ]; then
    echo -e "\n${C_BOLD}--> Checking ${#SH_FILES[@]} modified shell script(s) with shellcheck...${C_RESET}"
    if command -v shellcheck >/dev/null 2>&1; then
        for sh in "${SH_FILES[@]}"; do
            if shellcheck -x "$sh"; then
                echo -e "  ${C_GREEN}[PASS]${C_RESET} $sh"
            else
                echo -e "  ${C_RED}[FAIL]${C_RESET} $sh"
                ERRORS=$((ERRORS + 1))
            fi
        done
    else
        echo -e "  ${C_YELLOW}[WARN]${C_RESET} shellcheck not found on host, skipping."
    fi
else
    echo "  (No modified shell scripts)"
fi

# 2. Python Check (darker & ruff)
if [ ${#PY_FILES[@]} -gt 0 ]; then
    echo -e "\n${C_BOLD}--> Checking ${#PY_FILES[@]} modified Python file(s)...${C_RESET}"
    
    # Check formatting of changed lines with darker
    if command -v darker >/dev/null 2>&1; then
        echo "  • Checking format on modified lines (darker)..."
        DARKER_ARGS=("--check" "--diff")
        if [ "$MODE" = "branch" ]; then
            DARKER_ARGS+=("-r" "${BASE_REF}..HEAD")
        fi
        if ! darker "${DARKER_ARGS[@]}" "${PY_FILES[@]}"; then
            echo -e "  ${C_RED}[FAIL]${C_RESET} Python formatting issues detected in diff! Run 'darker <files>' to fix."
            ERRORS=$((ERRORS + 1))
        else
            echo -e "  ${C_GREEN}[PASS]${C_RESET} Python diff formatting is clean."
        fi
    else
        echo -e "  ${C_YELLOW}[WARN]${C_RESET} darker not found, skipping diff format check (install with 'make install-dev')."
    fi

    # Check linting on modified files with ruff
    if command -v ruff >/dev/null 2>&1; then
        echo "  • Checking linting on modified files (ruff)..."
        if ! ruff check "${PY_FILES[@]}"; then
            echo -e "  ${C_RED}[FAIL]${C_RESET} ruff linting errors found!"
            ERRORS=$((ERRORS + 1))
        else
            echo -e "  ${C_GREEN}[PASS]${C_RESET} ruff lint checks passed."
        fi
    else
        echo -e "  ${C_YELLOW}[WARN]${C_RESET} ruff not found, skipping ruff check."
    fi
else
    echo "  (No modified Python files)"
fi

# 3. Java Check (gradle compileJava if Java files modified)
if [ ${#JAVA_FILES[@]} -gt 0 ]; then
    echo -e "\n${C_BOLD}--> Checking ${#JAVA_FILES[@]} modified Java file(s)...${C_RESET}"
    if [ -d "pdns-output" ] && [ -x "pdns-output/gradlew" ]; then
        echo "  • Verifying pdns-output compiles..."
        if (cd pdns-output && ./gradlew compileJava -q); then
            echo -e "  ${C_GREEN}[PASS]${C_RESET} Java compilation succeeded."
        else
            echo -e "  ${C_RED}[FAIL]${C_RESET} Java compilation failed!"
            ERRORS=$((ERRORS + 1))
        fi
    fi
else
    echo "  (No modified Java files)"
fi

# Summary
echo ""
if [ "$ERRORS" -gt 0 ]; then
    echo -e "${C_RED}${C_BOLD}[FAIL] Diff-linting completed with $ERRORS error(s).${C_RESET}" >&2
    exit 1
else
    echo -e "${C_GREEN}${C_BOLD}[OK] All diff-lint checks passed!${C_RESET}"
    exit 0
fi
