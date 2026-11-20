#!/bin/bash
#
# suites/test-zone-profile-conflicts.sh
# E2E Test: Zone profile template inheritance, parent record precedence, and profile conflict resolution
#

set -eu
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=dim-testsuite/e2e/test-lib.sh
source "${SCRIPT_DIR}/../test-lib.sh"
setup_env

PROF_PARENT="prof-parent-eval"
PROF_CHILD="prof-child-eval"
PARENT_ZONE="prof-precedence.com"
CHILD_ZONE="sub.${PARENT_ZONE}"

cleanup_prof_conflicts() {
    delete_zone_all_views "$CHILD_ZONE"
    delete_zone_all_views "$PARENT_ZONE"
    ndcli delete zone-profile "$PROF_CHILD" --quiet 2>/dev/null || true
    ndcli delete zone-profile "$PROF_PARENT" --quiet 2>/dev/null || true
}

step "Clean up previous runs"
cleanup_prof_conflicts

step "Create parent and child zone profiles with distinct template records"
ndcli create zone-profile "$PROF_PARENT" primary "ns1.parent-tmpl.example.org." mail "hostmaster@parent-tmpl.example.org" --quiet
ndcli modify zone-profile "$PROF_PARENT" create rr @ NS "ns1.parent-tmpl.example.org." --quiet
ndcli modify zone-profile "$PROF_PARENT" create rr @ MX 10 "mail.parent-tmpl.example.org." --quiet

ndcli create zone-profile "$PROF_CHILD" primary "ns1.child-tmpl.example.org." mail "hostmaster@child-tmpl.example.org" --quiet
ndcli modify zone-profile "$PROF_CHILD" create rr @ NS "ns1.child-tmpl.example.org." --quiet
ndcli modify zone-profile "$PROF_CHILD" create rr @ MX 50 "tmpl-fallback.child-tmpl.example.org." --quiet

step "Instantiate parent zone from parent profile"
ndcli create zone "$PARENT_ZONE" profile "$PROF_PARENT" --quiet
ensure_output_sync "$PARENT_ZONE"

PARENT_MX=$(ndcli list rrs "${PARENT_ZONE}." MX -H | awk '{print $6}')
assert_equals "Parent zone inherited template MX" "mail.parent-tmpl.example.org." "$PARENT_MX" "Mail Server"

step "Add custom specific MX record in parent for future subzone"
# Pre-define a specific MX in parent for the subdomain
ndcli create rr "${CHILD_ZONE}." MX 5 "custom-precedence.sub.example.org." --quiet

step "Instantiate child subzone from child profile"
ndcli create zone "$CHILD_ZONE" profile "$PROF_CHILD" --quiet
ensure_output_sync "$CHILD_ZONE"

step "Assert: Parent record precedence - Custom MX preserved alongside or over profile default"
CHILD_MX_LIST=$(ndcli list rrs "${CHILD_ZONE}." MX -H)
echo "$CHILD_MX_LIST"

assert_contains "Custom record migrated from parent" "custom-precedence.sub.example.org." "$CHILD_MX_LIST"

step "Wait for PowerDNS to receive subzone and verify MX resolution"
SYNCED=false
PDNS_MX=""
for _ in $(seq 1 20); do
    PDNS_MX=$(dig "@${PDNS_ADDRESS:-127.1.1.1}" "${CHILD_ZONE}." MX +short)
    if echo "$PDNS_MX" | grep -q "custom-precedence.sub.example.org."; then
        SYNCED=true
        break
    fi
    sleep 1
done

assert_equals "PowerDNS answers with custom precedence MX" "true" "$SYNCED"

step "Cleanup"
cleanup_prof_conflicts

log_pass "Zone profile precedence and conflict resolution test passed."
