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

echo "### starting the dim server"
FLASK_APP='dim:create_app(db_mode="TEST")' flask db clear
FLASK_APP='dim:create_app(db_mode="TEST")' flask run --port 5000 >/tmp/dim.log 2>&1 &
until bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; do sleep 1; done

echo "### starting pdns-output ($PROPS)"
java -jar /opt/dim/pdns-output.jar --config "$PROPS" >/tmp/pdns-output.log 2>&1 &
jar_pid=$!
trap 'kill $jar_pid 2>/dev/null || true' EXIT

ndcli login -p p
ndcli create output out plugin pdns-db db-uri "$PDNS_DB_URI"
ndcli create zone-group zg
ndcli modify output out add zone-group zg

wait_for_queue() {
    local left=unknown
    for _ in $(seq 120); do
        left=$(mysql -h127.0.0.1 -P3307 -udim -pdim -N dim \
               -e 'select count(*) from outputupdate')
        [ "$left" = "0" ] && return 0
        sleep 1
    done
    echo "pdns-output did not drain the queue ($left left):" >&2
    tail -20 /tmp/pdns-output.log >&2
    return 1
}

# $1: zone name, rest: extra arguments for "ndcli modify zone ... dnssec enable"
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

check_zone nsec.com
check_zone nsec3.com nsec3 10
# NSEC3 with a salt and a high iteration count
check_zone nsec3-salt.com nsec3 100 salted

echo
echo "### pdns-output log (last lines)"
tail -3 /tmp/pdns-output.log
echo
echo "### all dnssec zones verified"
