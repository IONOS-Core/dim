#!/bin/bash
#
# End-to-end DNSSEC check for pdns-output: dim generates the keys, pdns-output
# signs the zone into the pdns database, pdns serves it and the result is
# validated twice - with drill (chain of trust, denial of existence) and with
# jdnssec-verifyzone (every RRSIG in the zone).
#
# Run it with:  docker compose run --rm testsuite dnssec
#
set -eu

cd /src/dim-testsuite

export PATH=/src/dim-testsuite/virtual_env/bin:$PATH
# ndcli keeps its session in ~/.ndcli.cookie.$NDCLI_USERNAME, so every
# invocation has to agree on the user
export NDCLI_SERVER=http://localhost:5000
export NDCLI_USERNAME=admin
export DIM_CONFIG=${DIM_CONFIG:-/etc/dim/dim.cfg}

PDNS_DB_URI=${PDNS_DB_URI:-mysql://pdns:pdns@127.0.0.1:3307/pdns1}
PDNS_ADDRESS=${PDNS_ADDRESS:-127.1.1.1}
PROPS=${PROPS:-tests/pdns-output-test.properties}
MYSQL_CMD="mysql -h127.0.0.1 -P3307 -udim -pdim -N dim"
PDNS_MYSQL_CMD="mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1"

sql_command() {
    local query=$1
    $MYSQL_CMD -e "${query}"
}

pdns_sql() {
    local query=$1
    $PDNS_MYSQL_CMD -N -e "${query}"
}

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

if bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; then
    echo "### Reusing running DIM server on port 5000"
    FLASK_APP='dim:create_app(db_mode="TEST")' flask db clear >/dev/null 2>&1
    pdns_sql "DELETE FROM records; DELETE FROM domainmetadata; DELETE FROM cryptokeys; DELETE FROM comments; DELETE FROM domains;"
else
    echo "### Starting DIM server on port 5000"
    FLASK_APP='dim:create_app(db_mode="TEST")' flask db clear >/dev/null 2>&1
    pdns_sql "DELETE FROM records; DELETE FROM domainmetadata; DELETE FROM cryptokeys; DELETE FROM comments; DELETE FROM domains;"
    FLASK_APP='dim:create_app(db_mode="TEST")' flask run --port 5000 >/tmp/dim.log 2>&1 &
    flask_pid=$!
    until bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; do sleep 1; done
fi

is_pdns_running() {
    pgrep -f "pdns-output\.jar" >/dev/null 2>&1 && return 0
    local lock
    lock=$(sql_command "SELECT IS_USED_LOCK('pdns_poller')" 2>/dev/null || true)
    [ -n "$lock" ] && [ "$lock" != "NULL" ]
}

if is_pdns_running; then
    echo -e "${RED}${BOLD}[ERROR] pdns-output is already running (DB lock 'pdns_poller' held)!${RESET}" >&2
    echo -e "        dnssec-check.sh tests gatekeeper signature refusals and needs dedicated output logs." >&2
    echo -e "        Please stop dim-output before running this script directly in the testbed.${RESET}" >&2
    exit 1
fi

echo "### Starting pdns-output ($PROPS) using $PDNS_OUTPUT_JAR"
rm -f /tmp/pdns-output.log
java -jar "$PDNS_OUTPUT_JAR" --config "$PROPS" >/tmp/pdns-output.log 2>&1 &
jar_pid=$!
cleanup() {
    if [ -n "${jar_pid:-}" ] && kill -0 "$jar_pid" 2>/dev/null; then
        kill "$jar_pid" 2>/dev/null
    fi
    if [ -n "${flask_pid:-}" ] && kill -0 "$flask_pid" 2>/dev/null; then
        kill "$flask_pid" 2>/dev/null
    fi
}
trap cleanup EXIT

ndcli login -p p >/dev/null 2>&1
ndcli show output out >/dev/null 2>&1 || ndcli create output out plugin pdns-db db-uri "$PDNS_DB_URI" >/dev/null
ndcli show zone-group zg >/dev/null 2>&1 || ndcli create zone-group zg >/dev/null
ndcli show output out | grep -q zg || ndcli modify output out add zone-group zg >/dev/null

# Polls the DIM database outputupdate queue until all transactions have been
# processed by pdns-output (queue count reaches 0), timing out after 120 seconds.
wait_for_queue() {
    local left=unknown
    for _ in $(seq 120); do
        left=$(sql_command 'select count(*) from outputupdate')
        [ "$left" = "0" ] && return 0
        sleep 1
    done
    echo "pdns-output did not drain the queue ($left left):" >&2
    tail -20 /tmp/pdns-output.log >&2
    return 1
}

wait_for_log() {
    local pattern="$1"
    local timeout="${2:-15}"
    local start
    start=$(date +%s)
    while true; do
        if grep -q "$pattern" /tmp/pdns-output.log 2>/dev/null; then
            return 0
        fi
        if [ $(( $(date +%s) - start )) -gt "$timeout" ]; then
            echo "Timeout waiting for '$pattern' in /tmp/pdns-output.log" >&2
            return 1
        fi
        sleep 0.5
    done
}

# Performs an end-to-end provisioning, signing, and double-validation of a DNSSEC zone:
# 1. Creates the zone in DIM, attaches it to zone-group 'zg', and enables DNSSEC (RSASHA256).
# 2. Creates sample resource records ('t' A and 'txt' TXT) to populate the zone.
# 3. Waits for pdns-output to drain the queue and sync records into PowerDNS.
# 4. Inspects PowerDNS database tables ('records' and 'domainmetadata') to confirm data was written.
# 5. Validates chain-of-trust (positive resolution) and denial of existence using 'drill'.
# 6. Dumps the zone via AXFR and cryptographically verifies every RRSIG using 'jdnssec-verifyzone'.
#
# Arguments:
#   $1: zone name (e.g. nsec.com, nsec3.com)
#   rest: extra arguments for "ndcli modify zone <zone> dnssec enable" (e.g. nsec3 10)
check_zone() {
    local zone=$1
    shift

    echo
    echo "######## $zone: dnssec enable 8 ksk 2048 zsk 1024 $*"
    ndcli create zone "$zone" >/dev/null
    ndcli modify zone-group zg add zone "$zone"
    ndcli modify zone "$zone" dnssec enable 8 ksk 2048 zsk 1024 "$@"
    ndcli modify zone "$zone" create rr t A 1.1.1.1 >/dev/null
    ndcli modify zone "$zone" create rr txt TXT "hello" >/dev/null
    wait_for_queue

    echo "--- records exported to the pdns database"
    pdns_sql "select concat(records.type, '=', count(*)) from records
         join domains on domains.id=records.domain_id
         where domains.name='$zone' group by records.type order by records.type" | tr '\n' ' '
    echo
    echo "--- domainmetadata"
    pdns_sql "select concat(kind, '=', content) from domainmetadata
         join domains on domains.id=domainmetadata.domain_id
         where domains.name='$zone'" | tr '\n' ' '
    echo

    ndcli list zone "$zone" dnskeys --rr > /tmp/trusted-key.key
    echo "--- drill: chain of trust for t.$zone"
    drill "@$PDNS_ADDRESS" "t.$zone" A -S -k /tmp/trusted-key.key | grep ';; Chase'
    echo "--- drill: denial of existence for no.$zone"
    drill "@$PDNS_ADDRESS" "no.$zone" A -S -k /tmp/trusted-key.key \
        | grep -E ';; Chase|Existence is denied' | sort -u

    echo "--- jdnssec-verifyzone over the axfr"
    dig +onesoa +noall +answer axfr "$zone" "@$PDNS_ADDRESS" > "/tmp/$zone.axfr"
    # an empty zone file "verifies" just fine, so make sure there is something
    # in it before trusting the result
    local rrsigs nsecs
    rrsigs=$(grep RRSIG "/tmp/$zone.axfr" | wc -l)
    nsecs=$(grep -E 'NSEC3?[[:space:]]' "/tmp/$zone.axfr" | wc -l)
    if [ "$rrsigs" -lt 1 ]; then
        echo "no RRSIGs in the axfr of $zone:" >&2
        cat "/tmp/$zone.axfr" >&2
        return 1
    fi
    echo "$rrsigs RRSIGs, $nsecs NSEC/NSEC3 records"
    java -cp "$PDNS_OUTPUT_JAR" com.verisignlabs.dnssec.cl.VerifyZone \
        "/tmp/$zone.axfr"
}

step "[1/7] Provisioning and validating initial zones (nsec, nsec3, nsec3-salt)"
check_zone nsec.com
check_zone nsec3.com nsec3 10
# NSEC3 with a salt and a high iteration count
check_zone nsec3-salt.com nsec3 100 salted
log_pass "All initial zones provisioned, signed, and verified"

step "[2/7] Testing update-validity renewal cycle end-to-end"
echo "--- timestamps before aging:"
sql_command "SELECT name, valid_begin, valid_end FROM zone WHERE name='nsec.com';"

echo "--- aging nsec.com and nsec3.com by 13 days in dim database (~93% elapsed validity, 1 day left)"
sql_command "UPDATE zone SET valid_begin = valid_begin - INTERVAL 13 DAY, valid_end = valid_end - INTERVAL 13 DAY WHERE name IN ('nsec.com', 'nsec3.com');"

echo "--- verified aged timestamps in dim database:"
sql_command "SELECT name, valid_begin, valid_end FROM zone WHERE name='nsec.com';"

echo "--- creating new record 'near.nsec.com' on 93% aged zone via ndcli"
ndcli modify zone nsec.com create rr near A 3.3.3.3
wait_for_queue

echo "--- verifying near-expired record and RRSIG in PowerDNS"
pdns_sql "SELECT content FROM records WHERE name='near.nsec.com' AND type='A';"
ndcli list zone nsec.com dnskeys --rr > /tmp/trusted-key.key
drill "@$PDNS_ADDRESS" "near.nsec.com" A -S -k /tmp/trusted-key.key | grep ';; Chase successful'
log_pass "Incremental CREATE_RR on near-expired zone (~93% aged) passed gatekeeper and verified"

echo "--- verifying that drill detects record tampering (DNS cache poisoning simulation)"
pdns_sql "UPDATE records SET content = '6.6.6.6' WHERE name = 'near.nsec.com' AND type = 'A';"
if ! drill "@$PDNS_ADDRESS" "near.nsec.com" A -S -k /tmp/trusted-key.key | grep -q ';; Chase failed.'; then
    log_fail "CRITICAL SECURITY FLAW: drill failed to reject tampered DNS record!"
fi
log_pass "drill successfully detected record tampering and rejected with 'Chase failed'"

# Restore authentic record content and verify resolution succeeds again
pdns_sql "UPDATE records SET content = '3.3.3.3' WHERE name = 'near.nsec.com' AND type = 'A';"
drill "@$PDNS_ADDRESS" "near.nsec.com" A -S -k /tmp/trusted-key.key | grep ';; Chase successful'
log_pass "Authentic record verified with 'Chase successful' after restoring content"

echo "--- expiring nsec.com by pushing end timestamp 1 day into the past"
sql_command "UPDATE zone SET valid_end = NOW() - INTERVAL 1 DAY WHERE name = 'nsec.com';"

echo "--- queueing record creation on expired zone"
out_id=$(sql_command "SELECT id FROM output WHERE name='out' LIMIT 1;")
sql_command "INSERT INTO outputupdate (zone_name, action, output_id, transaction, serial, name, ttl, type, content)
VALUES ('nsec.com', 'create_rr', $out_id, 'p_exp_0000000001', 2026110599, 'expired.nsec.com', 86400, 'A', '4.4.4.4');"

if ! wait_for_log "Refusing signature update for zone nsec.com"; then
    log_fail "Gatekeeper failed to refuse update on expired zone!"
fi
expired_leak=$(pdns_sql "SELECT COUNT(*) FROM records WHERE name='expired.nsec.com'")
if [ "$expired_leak" -ne 0 ]; then
    log_fail "Record on expired zone was written to PowerDNS!"
fi
log_pass "Gatekeeper successfully refused update on expired zone (expiration in past)"

# Clean up the test transaction and restore valid_end so renewal cycle can proceed
sql_command "DELETE FROM outputupdate WHERE transaction = 'p_exp_0000000001';"
sql_command "UPDATE zone SET valid_end = valid_begin + INTERVAL 14 DAY WHERE name = 'nsec.com';"

echo "--- running manage_dim update-validity -v"
manage_dim update-validity -v

echo "--- waiting for pdns-output to process renewals"
wait_for_queue

for zone in nsec.com nsec3.com; do
    echo "--- verifying renewed zone $zone with drill and jdnssec-verifyzone"
    ndcli list zone "$zone" dnskeys --rr > /tmp/trusted-key.key
    drill "@$PDNS_ADDRESS" "t.$zone" A -S -k /tmp/trusted-key.key | grep ';; Chase'
    dig +onesoa +noall +answer axfr "$zone" "@$PDNS_ADDRESS" > "/tmp/$zone-renewed.axfr"
    java -cp "$PDNS_OUTPUT_JAR" com.verisignlabs.dnssec.cl.VerifyZone "/tmp/$zone-renewed.axfr"
done
log_pass "Scheduled validity renewal completed and validated in PowerDNS"

step "[3/7] Testing pdns-output gatekeeper on full zone update (poison pill dnssec_update)"
# 1. Capture current healthy SOA RRSIG and serial from PowerDNS
current_rrsig=$(dig +onesoa +noall +answer axfr nsec3.com "@$PDNS_ADDRESS" | grep 'SOA.*RRSIG')
current_serial=$(pdns_sql "SELECT content FROM records WHERE name='nsec3.com' AND type='SOA'" | awk '{print $3}')

# Retrieve dynamic output_id for 'out'
out_id=$(sql_command "SELECT id FROM output WHERE name='out' LIMIT 1;")

# 2. Inject poison pill: set valid_begin 2 hours in the FUTURE (simulating clock skew)
sql_command "UPDATE zone SET valid_begin = NOW() + INTERVAL 2 HOUR WHERE name = 'nsec3.com';"

# 3. Queue update transaction for pdns-output
sql_command "INSERT INTO outputupdate (zone_name, action, output_id, transaction, serial)
VALUES ('nsec3.com', 'dnssec_update', $out_id, 'p_dnssec_0000001', 2026110401);"

# 4. Verify gatekeeper refused the future-dated signature in the log
echo "--- verifying gatekeeper logged refusal for dnssec_update"
if ! wait_for_log "Refusing signature update for zone nsec3.com"; then
    log_fail "pdns-output gatekeeper did not log expected refusal!"
fi
log_pass "Gatekeeper intercepted future inception on dnssec_update and logged refusal"

# 5. Verify PowerDNS records were NOT overwritten (healthy RRSIG preserved)
echo "--- verifying PowerDNS preserved existing healthy RRSIG"
new_rrsig=$(dig +onesoa +noall +answer axfr nsec3.com "@$PDNS_ADDRESS" | grep 'SOA.*RRSIG')
if [ "$current_rrsig" != "$new_rrsig" ]; then
    log_fail "PowerDNS records were modified despite invalid timestamps!"
fi
log_pass "PowerDNS preserved existing healthy RRSIGs without corruption"

# 6. Verify resolver validation still succeeds on the preserved records
echo "--- verifying resolver validation continues to pass"
drill "@$PDNS_ADDRESS" "t.nsec3.com" A -S -k /tmp/trusted-key.key | grep ';; Chase'
log_pass "Resolver chain-of-trust validation holds on preserved records"

# 7. Verify transaction remains queued in outputupdate (Fail-stop invariant)
pending_count=$(sql_command "SELECT COUNT(*) FROM outputupdate WHERE transaction='p_dnssec_0000001';")
if [ "$pending_count" -ne 1 ]; then
    log_fail "Queue dropped failed dnssec_update transaction! Pending count: $pending_count"
fi
log_pass "Fail-stop invariant confirmed: dnssec_update remains queued for recovery"

step "[4/7] Testing healing and recovery of full zone update"
# Heal the zone by setting valid_begin back to 1 hour in the past (RFC 6781 standard)
sql_command "UPDATE zone SET valid_begin = NOW() - INTERVAL 1 HOUR WHERE name = 'nsec3.com';"

# pdns-output worker will retry and now drain the queue
echo "--- waiting for pdns-output to drain queued dnssec_update after healing"
wait_for_queue
log_pass "Full zone update successfully drained after timestamp correction"

drill "@$PDNS_ADDRESS" "t.nsec3.com" A -S -k /tmp/trusted-key.key | grep ';; Chase'
log_pass "Resolver validation confirmed healthy on healed zone"

step "[5/7] Testing gatekeeper atomicity and rollback on incremental CREATE_RR"
# Capture baseline SOA serial
current_serial=$(pdns_sql "SELECT content FROM records WHERE name='nsec3.com' AND type='SOA'" | awk '{print $3}')

# 1. Poison pill: set valid_begin 2 hours in the FUTURE again
sql_command "UPDATE zone SET valid_begin = NOW() + INTERVAL 2 HOUR WHERE name = 'nsec3.com';"

# 2. Queue an incremental CREATE_RR transaction with a bumped serial:
sql_command "INSERT INTO outputupdate (zone_name, action, output_id, transaction, serial, name, ttl, type, content)
VALUES ('nsec3.com', 'create_rr', $out_id, 'p_rr_00000000001', 2026110499, 'leak.nsec3.com', 86400, 'A', '1.2.3.99');"

# 3. Wait specifically for gatekeeper refusal on THIS transaction
echo "--- verifying gatekeeper logged refusal for incremental CREATE_RR"
if ! wait_for_log "Error processing transaction p_rr_00000000001"; then
    log_fail "Gatekeeper did not attempt and refuse incremental CREATE_RR transaction!"
fi
log_pass "Gatekeeper intercepted incremental CREATE_RR and logged refusal"

# 4. Verify PowerDNS does NOT contain the raw, unsigned record:
leak_count=$(pdns_sql "SELECT COUNT(*) FROM records WHERE name='leak.nsec3.com'")
if [ "$leak_count" -ne 0 ]; then
    log_fail "CRITICAL BUG: Unsigned record was inserted into a signed zone!"
fi

# 5. Verify PowerDNS SOA serial was NOT bumped prematurely:
new_serial=$(pdns_sql "SELECT content FROM records WHERE name='nsec3.com' AND type='SOA'" | awk '{print $3}')
if [ "$new_serial" != "$current_serial" ]; then
    log_fail "CRITICAL BUG: SOA serial was modified in PowerDNS despite refused signature!"
fi
log_pass "PowerDNS atomic rollback verified (no record leak and no premature serial bump)"

# 6. Verify transaction remains queued in outputupdate (Fail-stop invariant)
pending_count=$(sql_command "SELECT COUNT(*) FROM outputupdate WHERE transaction='p_rr_00000000001';")
if [ "$pending_count" -ne 1 ]; then
    log_fail "Queue dropped failed incremental transaction! Pending count: $pending_count"
fi
log_pass "Fail-stop invariant confirmed: CREATE_RR remains queued for recovery"

step "[6/7] Testing healing and recovery of incremental CREATE_RR"
# Heal the zone by setting valid_begin back to 1 hour in the past (RFC 6781 standard)
sql_command "UPDATE zone SET valid_begin = NOW() - INTERVAL 1 HOUR WHERE name = 'nsec3.com';"

# pdns-output worker will retry and now drain the queue
echo "--- waiting for pdns-output to drain queued incremental CREATE_RR after healing"
wait_for_queue
log_pass "Queue completely drained after timestamp correction"

# Verify that leak.nsec3.com is now successfully published and signed with RRSIG
healed_leak=$(pdns_sql "SELECT COUNT(*) FROM records WHERE name='leak.nsec3.com' AND type='A'")
if [ "$healed_leak" -ne 1 ]; then
    log_fail "Healed record leak.nsec3.com was not published to PowerDNS!"
fi
healed_rrsig=$(dig +onesoa +noall +answer axfr nsec3.com "@$PDNS_ADDRESS" | grep 'leak.nsec3.com.*RRSIG')
if [ -z "$healed_rrsig" ]; then
    log_fail "Record leak.nsec3.com was published but lacks RRSIG!"
fi
log_pass "Healed record successfully published with valid RRSIG"

step "[7/7] Testing UPDATE_SOA re-signs SOA record in signed zones"
# 1. Capture original SOA record content in PowerDNS
old_soa=$(pdns_sql "SELECT content FROM records WHERE name='nsec.com' AND type='SOA'")
echo "--- original SOA content in PowerDNS: $old_soa"

# 2. Modify SOA refresh timer via ndcli (queues UPDATE_SOA in DIM)
echo "--- modifying SOA refresh timer to 7200 via ndcli (triggers UPDATE_SOA)"
ndcli modify zone nsec.com set refresh 7200

# 3. Wait for pdns-output to process the UPDATE_SOA transaction
wait_for_queue

# 4. Verify SOA content was modified in PowerDNS
new_soa=$(pdns_sql "SELECT content FROM records WHERE name='nsec.com' AND type='SOA'")
echo "--- updated SOA content in PowerDNS: $new_soa"
if [ "$old_soa" = "$new_soa" ]; then
    log_fail "PowerDNS SOA record was not updated after modify zone set refresh!"
fi

# 5. Cryptographically verify that the updated SOA record was properly re-signed
echo "--- validating zone signatures over AXFR after UPDATE_SOA"
dig +onesoa +noall +answer axfr nsec.com "@$PDNS_ADDRESS" > "/tmp/nsec.com-soa-update.axfr"
if ! java -cp "$PDNS_OUTPUT_JAR" com.verisignlabs.dnssec.cl.VerifyZone "/tmp/nsec.com-soa-update.axfr"; then
    log_fail "CRITICAL BUG: SOA record was updated in PowerDNS but RRSIG was not re-signed!"
fi

# 6. Verify resolver validation of SOA record with drill
echo "--- validating SOA query chain-of-trust with drill"
ndcli list zone nsec.com dnskeys --rr > /tmp/trusted-key.key
if ! drill "@$PDNS_ADDRESS" nsec.com SOA -S -k /tmp/trusted-key.key | grep -q ';; Chase successful'; then
    log_fail "CRITICAL BUG: drill resolver failed to validate SOA RRset after UPDATE_SOA!"
fi
log_pass "SOA update was published with cryptographically valid RRSIG"

echo -e "\n${GREEN}${BOLD}========================================================================${RESET}"
echo -e "  ${GREEN}[SUCCESS] All DNSSEC and Gatekeeper test stages passed!${RESET}"
echo -e "${GREEN}${BOLD}========================================================================${RESET}\n"