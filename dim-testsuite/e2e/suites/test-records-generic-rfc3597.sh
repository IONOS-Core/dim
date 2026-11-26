#!/bin/bash
#
# suites/test-records-generic-rfc3597.sh
# E2E Test: Strict RR type whitelisting, RFC 3597 generic/unknown type rejection, and supported complex types
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env


cleanup_rfc3597() {
    delete_zone_all_views rfc3597-eval-test.com
}

step "Clean up previous runs"
cleanup_rfc3597

step "Create forward zone"
ndcli --quiet create zone rfc3597-eval-test.com
ensure_output_sync rfc3597-eval-test.com

step "Test 1: Guard - Generic RFC 3597 private type (TYPE65280) is strictly rejected by CLI parser"
set +e
TYPE_PRIVATE_OUT=$(ndcli create rr "srv.rfc3597-eval-test.com." TYPE65280 "\\# 4 01020304" 2>&1)
EXIT_PRIVATE=$?
set -e

assert_exit_code "Generic RFC 3597 private type rejected" 1 "$EXIT_PRIVATE" "$TYPE_PRIVATE_OUT"
assert_contains "Parser error message" "Invalid subcommand TYPE65280" "$TYPE_PRIVATE_OUT"

step "Test 2: Guard - Unimplemented standard type (e.g. TYPE65 / HTTPS) rejected"
set +e
TYPE_HTTPS_OUT=$(ndcli create rr "web.rfc3597-eval-test.com." TYPE65 "\\# 0" 2>&1)
EXIT_HTTPS=$?
set -e

assert_exit_code "Unimplemented RFC 3597 type rejected" 1 "$EXIT_HTTPS" "$TYPE_HTTPS_OUT"
assert_contains "Parser error message" "Invalid subcommand TYPE65" "$TYPE_HTTPS_OUT"

step "Test 3: Guard - Arbitrary unknown string type (BOGUS) rejected"
set +e
BOGUS_OUT=$(ndcli create rr "test.rfc3597-eval-test.com." BOGUS "some-payload" 2>&1)
EXIT_BOGUS=$?
set -e

assert_exit_code "Arbitrary unknown type rejected" 1 "$EXIT_BOGUS" "$BOGUS_OUT"
assert_contains "Parser error message" "Invalid subcommand BOGUS" "$BOGUS_OUT"

step "Test 4: Whitelisted complex types (TLSA, SSHFP, CAA) succeed cleanly"
ndcli --quiet create rr "_443._tcp.web.rfc3597-eval-test.com." TLSA 3 0 1 1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef
ndcli --quiet create rr "ssh.rfc3597-eval-test.com." SSHFP 1 1 1234567890abcdef1234567890abcdef12345678
ndcli --quiet create rr "rfc3597-eval-test.com." CAA 0 issue "letsencrypt.org"

# Assert all three whitelisted complex records exist in DIM
# shellcheck disable=SC2126 # grep|wc -l prevents set -e termination on 0 matches
TLSA_COUNT=$(ndcli list rrs "_443._tcp.web.rfc3597-eval-test.com." TLSA -H | grep "TLSA" | wc -l)
assert_equals "Whitelisted TLSA record created" "1" "$TLSA_COUNT" "Count"

# shellcheck disable=SC2126
SSHFP_COUNT=$(ndcli list rrs "ssh.rfc3597-eval-test.com." SSHFP -H | grep "SSHFP" | wc -l)
assert_equals "Whitelisted SSHFP record created" "1" "$SSHFP_COUNT" "Count"

# shellcheck disable=SC2126
CAA_COUNT=$(ndcli list rrs "rfc3597-eval-test.com." CAA -H | grep "CAA" | wc -l)
assert_equals "Whitelisted CAA record created" "1" "$CAA_COUNT" "Count"

step "Wait for PowerDNS to receive complex records from pdns-output"
SYNCED=false
for _ in $(seq 1 20); do
    CAA_ANS=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "rfc3597-eval-test.com." CAA +short)
    if [ -n "$CAA_ANS" ]; then
        SYNCED=true
        break
    fi
    sleep 1
done
assert_equals "PowerDNS received zone updates" "true" "$SYNCED"

step "Authoritative Resolution Validation in PowerDNS"
assert_contains "TLSA record resolution" "3 0 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "_443._tcp.web.rfc3597-eval-test.com." TLSA +short)"
assert_contains "SSHFP record resolution" "1 1" "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "ssh.rfc3597-eval-test.com." SSHFP +short)"
assert_contains "CAA record resolution" '0 issue "letsencrypt.org"' "$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "rfc3597-eval-test.com." CAA +short)"

step "Cleanup"
cleanup_rfc3597

log_pass "RR type whitelisting and unknown type rejection test passed."
