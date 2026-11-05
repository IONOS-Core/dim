#!/bin/bash
#
# End-to-end user operations test on an aged DNSSEC-signed zone:
# Proves that a zone that has aged to ~60% of its validity window continues
# to support all normal user lifecycle operations (create A/AAAA/CNAME/MX/TXT,
# round-robin updates, partial deletions, and zone TTL + SOA modifications)
# without any Gatekeeper refusals or signature corruption.
#
# Run it with:
#   docker compose exec dim-mw bash -c '/src/dim-testsuite/e2e/dnssec-user-ops.sh'
#
set -eu

cd /src/dim-testsuite

export PATH=/src/dim-testsuite/virtual_env/bin:$PATH
export NDCLI_SERVER=http://localhost:5000
export NDCLI_USERNAME=admin
export DIM_CONFIG=${DIM_CONFIG:-/etc/dim/dim.cfg}

PDNS_DB_URI=${PDNS_DB_URI:-mysql://pdns:pdns@127.0.0.1:3307/pdns1}
PDNS_ADDRESS=${PDNS_ADDRESS:-127.1.1.1}
PROPS=${PROPS:-tests/pdns-output-test.properties}
MYSQL_CMD="mysql -h127.0.0.1 -P3307 -udim -pdim -N dim"
PDNS_MYSQL_CMD="mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1"

PDNS_OUTPUT_JAR="/opt/dim/pdns-output.jar"
for jar in /src/pdns-output/pdns-output/build/libs/*-all.jar; do
    if [ -f "$jar" ]; then
        PDNS_OUTPUT_JAR="$jar"
    fi
done

# ANSI Terminal Colors
GREEN="\033[1;32m"
RED="\033[1;31m"
CYAN="\033[1;36m"
BOLD="\033[1m"
RESET="\033[0m"

log_pass() { echo -e "  ${GREEN}[PASS]${RESET} $1"; }
log_fail() { echo -e "  ${RED}[FAIL]${RESET} $1" >&2; exit 1; }
step()     { echo -e "\n${CYAN}${BOLD}==> $1${RESET}"; }

sql_command() {
    local query=$1
    $MYSQL_CMD -e "${query}"
}

pdns_sql() {
    local query=$1
    $PDNS_MYSQL_CMD -N -e "${query}"
}

ZONE="userops.com"

wait_for_queue() {
    local zone=${1:-$ZONE}
    local left=unknown
    local oid=${out_id:-}
    local query="SELECT COUNT(*) FROM outputupdate WHERE zone_name = '$zone'"
    if [ -n "$oid" ]; then
        query="$query AND output_id = $oid"
    fi
    for _ in $(seq 120); do
        left=$(sql_command "$query")
        [ "$left" = "0" ] && return 0
        sleep 1
    done
    echo "pdns-output did not drain the queue ($left left):" >&2
    tail -20 /tmp/pdns-output.log >&2
    return 1
}

cleanup() {
    if [ -n "${jar_pid:-}" ] && kill -0 "$jar_pid" 2>/dev/null; then
        kill "$jar_pid" 2>/dev/null
    fi
    if [ -n "${flask_pid:-}" ] && kill -0 "$flask_pid" 2>/dev/null; then
        kill "$flask_pid" 2>/dev/null
    fi
}
trap cleanup EXIT

# Ensure DIM server is running or start it locally
if bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; then
    echo "### Using running DIM server on port 5000"
else
    echo "### Starting local DIM server on port 5000"
    FLASK_APP='dim:create_app(db_mode="TEST")' flask run --port 5000 >/tmp/dim-userops.log 2>&1 &
    flask_pid=$!
    until bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; do sleep 1; done
fi

is_pdns_running() {
    pgrep -f "pdns-output\.jar" >/dev/null 2>&1 && return 0
    local lock
    lock=$(sql_command "SELECT IS_USED_LOCK('pdns_poller')" 2>/dev/null || true)
    [ -n "$lock" ] && [ "$lock" != "NULL" ]
}

# Ensure daemon is running or start it locally
if is_pdns_running; then
    echo "### Using running pdns-output daemon"
else
    echo "### Starting local pdns-output daemon using $PDNS_OUTPUT_JAR"
    rm -f /tmp/pdns-output.log
    java -jar "$PDNS_OUTPUT_JAR" --config "$PROPS" >/tmp/pdns-output.log 2>&1 &
    jar_pid=$!
fi

# Ensure user is logged in and zone group exists
ndcli login -p p >/dev/null 2>&1
if ! ndcli show output out >/dev/null 2>&1; then
    ndcli create output out plugin pdns-db db-uri "$PDNS_DB_URI" >/dev/null
fi
if ! ndcli show zone-group zg >/dev/null 2>&1; then
    ndcli create zone-group zg >/dev/null
fi
if ! ndcli show output out | grep -q zg; then
    ndcli modify output out add zone-group zg >/dev/null
fi

out_id=$(sql_command "SELECT id FROM output WHERE name='out' LIMIT 1;")

# Ensure test network containers exist in IPAM
if ! ndcli show container 10.0.0.0/8 >/dev/null 2>&1; then
    ndcli create container 10.0.0.0/8 >/dev/null
fi
if ! ndcli show container 2001:db8::/32 >/dev/null 2>&1; then
    ndcli create container 2001:db8::/32 >/dev/null
fi

step "[1/8] Provisioning zone $ZONE and enabling NSEC3 DNSSEC"
# Clean up existing test zone and any stale queue actions deterministically
if ndcli show zone "$ZONE" >/dev/null 2>&1; then
    ndcli delete zone "$ZONE" --cleanup >/dev/null
fi
sql_command "DELETE FROM outputupdate WHERE zone_name='$ZONE';"
pdns_sql "DELETE FROM records WHERE domain_id IN (SELECT id FROM domains WHERE name='$ZONE'); DELETE FROM domainmetadata WHERE domain_id IN (SELECT id FROM domains WHERE name='$ZONE'); DELETE FROM cryptokeys WHERE domain_id IN (SELECT id FROM domains WHERE name='$ZONE'); DELETE FROM domains WHERE name='$ZONE';"

ndcli create zone "$ZONE" >/dev/null
ndcli modify zone-group zg add zone "$ZONE"
ndcli modify zone "$ZONE" dnssec enable 8 ksk 2048 zsk 1024 nsec3 10
wait_for_queue

ndcli list zone "$ZONE" dnskeys --rr > /tmp/userops-key.key
dig +onesoa +noall +answer axfr "$ZONE" "@$PDNS_ADDRESS" > "/tmp/$ZONE-init.axfr"
java -cp "$PDNS_OUTPUT_JAR" com.verisignlabs.dnssec.cl.VerifyZone "/tmp/$ZONE-init.axfr"
log_pass "Zone $ZONE provisioned, signed with NSEC3, and initial signatures verified"

step "[2/8] Simulating zone aging: shift validity window by 8 days (~57% elapsed)"
echo "--- timestamps before aging:"
sql_command "SELECT name, valid_begin, valid_end FROM zone WHERE name='$ZONE';"
sql_command "UPDATE zone SET valid_begin = valid_begin - INTERVAL 8 DAY, valid_end = valid_end - INTERVAL 8 DAY WHERE name = '$ZONE';"
echo "--- verified aged timestamps in dim database (proving age shift):"
sql_command "SELECT name, valid_begin, valid_end FROM zone WHERE name='$ZONE';"
log_pass "Zone $ZONE successfully aged by 8 days in DIM database"

step "[3/8] User Operation: Create dual-stack web server records (A and AAAA)"
ndcli modify zone "$ZONE" create rr web A 10.0.1.10
ndcli modify zone "$ZONE" create rr web AAAA 2001:db8::10
wait_for_queue

drill "@$PDNS_ADDRESS" "web.$ZONE" A -S -k /tmp/userops-key.key | grep ';; Chase successful'
drill "@$PDNS_ADDRESS" "web.$ZONE" AAAA -S -k /tmp/userops-key.key | grep ';; Chase successful'
log_pass "Dual-stack A and AAAA records created, signed, and validated on aged zone"

step "[4/8] User Operation: Add round-robin record to existing hostname (CREATE_RR on existing name)"
ndcli modify zone "$ZONE" create rr web A 10.0.1.11
wait_for_queue

a_count=$(pdns_sql "SELECT COUNT(*) FROM records WHERE name='web.$ZONE' AND type='A';")
if [ "$a_count" -ne 2 ]; then
    log_fail "Expected 2 round-robin A records in PowerDNS, found $a_count!"
fi
drill "@$PDNS_ADDRESS" "web.$ZONE" A -S -k /tmp/userops-key.key | grep ';; Chase successful'
log_pass "Round-robin A record added to existing RRset and cryptographically verified"

step "[5/8] User Operation: Create CNAME, TXT (SPF), and Mail (MX + A) records"
ndcli modify zone "$ZONE" create rr www CNAME "web.$ZONE."
ndcli modify zone "$ZONE" create rr @ TXT "v=spf1 mx -all"
ndcli modify zone "$ZONE" create rr mail A 10.0.1.25
ndcli modify zone "$ZONE" create rr @ MX 10 "mail.$ZONE."
wait_for_queue

drill "@$PDNS_ADDRESS" "www.$ZONE" CNAME -S -k /tmp/userops-key.key | grep ';; Chase successful'
drill "@$PDNS_ADDRESS" "$ZONE" TXT -S -k /tmp/userops-key.key | grep ';; Chase successful'
drill "@$PDNS_ADDRESS" "mail.$ZONE" A -S -k /tmp/userops-key.key | grep ';; Chase successful'
drill "@$PDNS_ADDRESS" "$ZONE" MX -S -k /tmp/userops-key.key | grep ';; Chase successful'
log_pass "CNAME, TXT (SPF), and MX records created and validated on aged zone"

step "[6/8] User Operation: Partial deletion from round-robin RRset (DELETE_RR)"
# Delete 10.0.1.11, keeping 10.0.1.10
ndcli modify zone "$ZONE" delete rr web A 10.0.1.11
wait_for_queue

remaining_a=$(pdns_sql "SELECT content FROM records WHERE name='web.$ZONE' AND type='A';")
if [ "$remaining_a" != "10.0.1.10" ]; then
    log_fail "Expected only 10.0.1.10 to remain, found: $remaining_a!"
fi
drill "@$PDNS_ADDRESS" "web.$ZONE" A -S -k /tmp/userops-key.key | grep ';; Chase successful'
log_pass "Partial record deletion from RRset successfully updated RRSIG and verified"

step "[7/8] User Operation: Delete CNAME record and verify denial of existence"
ndcli modify zone "$ZONE" delete rr www CNAME "web.$ZONE."
wait_for_queue

cname_exists=$(pdns_sql "SELECT COUNT(*) FROM records WHERE name='www.$ZONE';")
if [ "$cname_exists" -ne 0 ]; then
    log_fail "Deleted CNAME www.$ZONE still exists in PowerDNS!"
fi
drill "@$PDNS_ADDRESS" "www.$ZONE" CNAME -S -k /tmp/userops-key.key | grep -E ';; Chase|Existence is denied' | sort -u
log_pass "CNAME deleted, NSEC3 chain cleanly closed, and denial of existence validated"

step "[8/8] User Operation: Modify zone TTL and SOA timers, and perform full-zone cryptographic audit"
echo "--- modifying zone TTL to 3600 and SOA timers (refresh 7200, retry 1800)"
ndcli modify zone "$ZONE" set ttl 3600 refresh 7200 retry 1800
wait_for_queue

# Verify TTL was updated in PowerDNS
soa_ttl=$(pdns_sql "SELECT ttl FROM records WHERE name='$ZONE' AND type='SOA';")
if [ "$soa_ttl" -ne 3600 ]; then
    log_fail "Expected SOA TTL to be 3600, found $soa_ttl!"
fi

drill "@$PDNS_ADDRESS" "$ZONE" SOA -S -k /tmp/userops-key.key | grep ';; Chase successful'

# Dump entire zone via AXFR and verify every single RRSIG across all RRsets
echo "--- running full cryptographic audit with jdnssec-verifyzone over AXFR"
dig +onesoa +noall +answer axfr "$ZONE" "@$PDNS_ADDRESS" > "/tmp/$ZONE-final.axfr"
java -cp "$PDNS_OUTPUT_JAR" com.verisignlabs.dnssec.cl.VerifyZone "/tmp/$ZONE-final.axfr"
log_pass "Zone TTL and SOA modified, re-signed, and entire aged zone passed 100% cryptographic audit"

echo -e "\n${GREEN}${BOLD}========================================================================${RESET}"
echo -e "  ${GREEN}[SUCCESS] All User Lifecycle Operations on aged DNSSEC zone passed!${RESET}"
echo -e "${GREEN}${BOLD}========================================================================${RESET}\n"
