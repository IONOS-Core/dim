#!/bin/bash
# Checks the health and status of all expected testbed services with colorized output.

GREEN="\033[1;32m"
RED="\033[1;31m"
BOLD="\033[1m"
RESET="\033[0m"

COMPOSE="docker compose -f dim-testsuite/compose.yaml"
EXPECTED_SERVICES="mariadb pdns1 pdns2 pdns-unit dim-mw dim-output"

echo -e "\n${BOLD}==> Testbed Services Status:${RESET}"

# Query states of all services from docker compose
RAW_STATES=$($COMPOSE ps -a --format "{{.Service}}:{{.State}}")

ALL_HEALTHY=1

for svc in $EXPECTED_SERVICES; do
    STATE=$(echo "$RAW_STATES" | grep -E "^${svc}:" | head -n 1 | cut -d: -f2)
    if [ "$STATE" = "running" ]; then
        echo -e "  ${GREEN}[OK]${RESET}   ${svc} : running"
    elif [ -n "$STATE" ]; then
        ALL_HEALTHY=0
        echo -e "  ${RED}[FAIL]${RESET} ${svc} : ${STATE}"
    else
        ALL_HEALTHY=0
        echo -e "  ${RED}[FAIL]${RESET} ${svc} : missing"
    fi
done

if [ "$ALL_HEALTHY" -eq 1 ]; then
    echo -e "\n${GREEN}${BOLD}[SUCCESS] All expected testbed services are healthy and running!${RESET}\n"
    exit 0
else
    echo -e "\n${RED}${BOLD}[WARNING] One or more testbed services are not running!${RESET}"
    echo -e "          Run 'make testbed-start' to start or recover missing services.\n"
    exit 1
fi
