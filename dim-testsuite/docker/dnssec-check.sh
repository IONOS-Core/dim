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

PDNS_DB_URI=${PDNS_DB_URI:-mysql://pdns:pdns@127.0.0.1:3307/pdns1}
PDNS_ADDRESS=${PDNS_ADDRESS:-127.1.1.1}
PROPS=${PROPS:-tests/pdns-output-test.properties}
MYSQL_CMD="mysql -h127.0.0.1 -P3307 -udim -pdim -N dim"

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
    mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -e \
        "DELETE FROM records; DELETE FROM domainmetadata; DELETE FROM cryptokeys; DELETE FROM comments; DELETE FROM domains;" >/dev/null 2>&1 || true
else
    echo "### Starting DIM server on port 5000"
    FLASK_APP='dim:create_app(db_mode="TEST")' flask db clear >/dev/null 2>&1
    mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -e \
        "DELETE FROM records; DELETE FROM domainmetadata; DELETE FROM cryptokeys; DELETE FROM comments; DELETE FROM domains;" >/dev/null 2>&1 || true
    FLASK_APP='dim:create_app(db_mode="TEST")' flask run --port 5000 >/tmp/dim.log 2>&1 &
    until bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; do sleep 1; done
fi

if pgrep -f "pdns-output\.jar" >/dev/null 2>&1; then
    echo "### Reusing running pdns-output daemon"
else
    echo "### Starting pdns-output ($PROPS)"
    java -jar /opt/dim/pdns-output.jar --config "$PROPS" >/tmp/pdns-output.log 2>&1 &
    jar_pid=$!
    trap 'kill $jar_pid 2>/dev/null || true' EXIT
fi

ndcli login -p p >/dev/null 2>&1
ndcli create output out plugin pdns-db db-uri "$PDNS_DB_URI" >/dev/null 2>&1 || true
ndcli create zone-group zg >/dev/null 2>&1 || true
ndcli modify output out add zone-group zg >/dev/null 2>&1 || true


sql_command() {
    local query=$1
    $MYSQL_CMD -e "${query}"
}

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
    ndcli create zone "$zone" 2>&1 | grep -v '^WARNING' || true
    ndcli modify zone-group zg add zone "$zone"
    ndcli modify zone "$zone" dnssec enable 8 ksk 2048 zsk 1024 "$@"
    ndcli modify zone "$zone" create rr t A 1.1.1.1 >/dev/null
    ndcli modify zone "$zone" create rr txt TXT "hello" >/dev/null
    wait_for_queue

    echo "--- records exported to the pdns database"
    mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
        "select concat(records.type, '=', count(*)) from records
         join domains on domains.id=records.domain_id
         where domains.name='$zone' group by records.type order by records.type" | tr '\n' ' '
    echo
    echo "--- domainmetadata"
    mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
        "select concat(kind, '=', content) from domainmetadata
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
    rrsigs=$(grep -c RRSIG "/tmp/$zone.axfr" || true)
    nsecs=$(grep -cE 'NSEC3?[[:space:]]' "/tmp/$zone.axfr" || true)
    if [ "$rrsigs" -lt 1 ]; then
        echo "no RRSIGs in the axfr of $zone:" >&2
        cat "/tmp/$zone.axfr" >&2
        return 1
    fi
    echo "$rrsigs RRSIGs, $nsecs NSEC/NSEC3 records"
    java -cp /opt/dim/pdns-output.jar com.verisignlabs.dnssec.cl.VerifyZone \
        "/tmp/$zone.axfr"
}

step "[1/4] Provisioning and validating initial zones (nsec, nsec3, nsec3-salt)"
check_zone nsec.com
check_zone nsec3.com nsec3 10
# NSEC3 with a salt and a high iteration count
check_zone nsec3-salt.com nsec3 100 salted
log_pass "All initial zones provisioned, signed, and verified"

step "[2/4] Testing update-validity renewal cycle end-to-end"
echo "--- aging nsec.com and nsec3.com by 8 days in dim database"
mysql -h127.0.0.1 -P3307 -udim -pdim dim -e \
    "UPDATE zone SET valid_begin = valid_begin - INTERVAL 8 DAY, valid_end = valid_end - INTERVAL 8 DAY WHERE name IN ('nsec.com', 'nsec3.com')"

echo "--- running manage_dim update-validity -v"
/src/dim/manage_dim update-validity -v

echo "--- waiting for pdns-output to process renewals"
wait_for_queue

for zone in nsec.com nsec3.com; do
    echo "--- verifying renewed zone $zone with drill and jdnssec-verifyzone"
    ndcli list zone "$zone" dnskeys --rr > /tmp/trusted-key.key
    drill "@$PDNS_ADDRESS" "t.$zone" A -S -k /tmp/trusted-key.key | grep ';; Chase'
    dig +onesoa +noall +answer axfr "$zone" "@$PDNS_ADDRESS" > "/tmp/$zone-renewed.axfr"
    java -cp /opt/dim/pdns-output.jar com.verisignlabs.dnssec.cl.VerifyZone "/tmp/$zone-renewed.axfr"
done
log_pass "Scheduled validity renewal completed and validated in PowerDNS"

step "[3/5] Testing pdns-output signature gatekeeper (poison pill test)"
# 1. Capture current healthy SOA RRSIG and serial from PowerDNS
current_rrsig=$(dig +onesoa +noall +answer axfr nsec3.com "@$PDNS_ADDRESS" | grep 'SOA.*RRSIG')
current_serial=$(mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
    "SELECT content FROM records WHERE name='nsec3.com' AND type='SOA'" | awk '{print $3}')

# 2. Inject poison pill: set valid_begin 2 hours in the FUTURE (simulating INC-135591)
sql_command "UPDATE zone SET valid_begin = NOW() + INTERVAL 2 HOUR WHERE name = 'nsec3.com';"

# 3. Queue update transaction for pdns-output
sql_command "INSERT INTO outputupdate (zone_name, action, output_id, transaction, serial)
VALUES ('nsec3.com', 'dnssec_update', 1, 'p_dnssec_0000001', 2026110401);"

# 4. Verify gatekeeper refused the future-dated signature in the log
echo "--- verifying gatekeeper logged refusal"
if ! wait_for_log "Refusing signature update for zone nsec3.com"; then
    log_fail "pdns-output gatekeeper did not log expected refusal!"
fi
log_pass "Gatekeeper intercepted future inception and logged refusal"

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

step "[4/5] Testing atomicity and rollback on incremental CREATE_RR"
# nsec3.com is signed, but has valid_begin in the future (getZSP will refuse).
# Queue an incremental CREATE_RR transaction with a bumped serial:
sql_command "INSERT INTO outputupdate (zone_name, action, output_id, transaction, serial, name, ttl, type, content)
VALUES ('nsec3.com', 'create_rr', 1, 'p_rr_00000000001', 2026110499, 'leak.nsec3.com', 86400, 'A', '1.2.3.99');"

# Wait for gatekeeper refusal log
if ! wait_for_log "Refusing signature update for zone nsec3.com"; then
    log_fail "Gatekeeper did not refuse incremental update!"
fi

# Verify PowerDNS does NOT contain the raw, unsigned record:
leak_count=$(mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
    "SELECT COUNT(*) FROM records WHERE name='leak.nsec3.com'")
if [ "$leak_count" -ne 0 ]; then
    log_fail "CRITICAL BUG: Unsigned record was inserted into a signed zone!"
fi

# Verify PowerDNS SOA serial was NOT bumped prematurely:
new_serial=$(mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
    "SELECT content FROM records WHERE name='nsec3.com' AND type='SOA'" | awk '{print $3}')
if [ "$new_serial" != "$current_serial" ]; then
    log_fail "CRITICAL BUG: SOA serial was modified in PowerDNS despite refused signature!"
fi
log_pass "PowerDNS atomic rollback verified (no record leak and no premature serial bump)"

# Verify transactions remain queued in outputupdate (Fail-stop invariant)
pending_count=$(sql_command "SELECT COUNT(*) FROM outputupdate WHERE zone_name='nsec3.com';")
if [ "$pending_count" -lt 2 ]; then
    log_fail "Queue dropped failed transactions! Pending count: $pending_count"
fi
log_pass "Fail-stop invariant confirmed: transactions remain queued for recovery"

step "[5/5] Testing healing and automatic recovery once timestamps are valid"
# Heal the zone by setting valid_begin back to 1 hour in the past (RFC 6781 standard)
sql_command "UPDATE zone SET valid_begin = NOW() - INTERVAL 1 HOUR WHERE name = 'nsec3.com';"

# pdns-output worker will retry and now drain the queue
echo "--- waiting for pdns-output to drain queued transactions after healing"
wait_for_queue
log_pass "Queue completely drained after timestamp correction"

# Verify that leak.nsec3.com is now successfully published and signed with RRSIG
healed_leak=$(mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1 -N -e \
    "SELECT COUNT(*) FROM records WHERE name='leak.nsec3.com' AND type='A'")
if [ "$healed_leak" -ne 1 ]; then
    log_fail "Healed record leak.nsec3.com was not published to PowerDNS!"
fi
healed_rrsig=$(dig +onesoa +noall +answer axfr nsec3.com "@$PDNS_ADDRESS" | grep 'leak.nsec3.com.*RRSIG')
if [ -z "$healed_rrsig" ]; then
    log_fail "Record leak.nsec3.com was published but lacks RRSIG!"
fi
log_pass "Healed record successfully published with valid RRSIG"

echo -e "\n${GREEN}${BOLD}========================================================================${RESET}"
echo -e "  ${GREEN}[SUCCESS] All DNSSEC and Gatekeeper test stages passed!${RESET}"
echo -e "${GREEN}${BOLD}========================================================================${RESET}\n"