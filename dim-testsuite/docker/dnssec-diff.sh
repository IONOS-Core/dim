#!/bin/bash
#
# Differential DNSSEC check: compares a previous/old pdns-output jar against the
# newly built jar over the very same dim database, and diffs what both write to
# the PowerDNS databases.
#
# The keys and the signature validity window come from one fixture and
# RSASHA256 signatures are deterministic, so for an unchanged behaviour every
# record, including every RRSIG, has to be byte for byte identical.
#
# Every jar runs once per JVM timezone (TZS) and per crypto implementation
# (CRYPTOS, "true" is the gmp based NativeRSAProvider production uses).
#
# Run it with:
#   docker compose run --rm -v ./baseline:/opt/baseline:ro -v ./out:/out testsuite dnssec-diff
#
# Environment variables:
#   OLD_JAR   Path to previous/reference jar (default: /opt/baseline/pdns-output-old.jar)
#   NEW_JAR   Path to newly built jar (default: /opt/dim/pdns-output.jar)
#   OLD_JAVA  Java binary for old jar (default: java, or /opt/java8/bin/java if comparing with 4.x)
#   NEW_JAVA  Java binary for new jar (default: java)
#   TZS       Timezones to test (default: UTC Europe/Berlin)
#   CRYPTOS   Crypto options (default: false true)
#
set -eu

cd /src/dim-testsuite

export PATH=/src/dim-testsuite/virtual_env/bin:$PATH
export NDCLI_SERVER=http://localhost:5000
export NDCLI_USERNAME=admin
export FLASK_APP='dim:create_app(db_mode="TEST")'

OLD_JAR=${OLD_JAR:-/opt/baseline/pdns-output-old.jar}
NEW_JAR=${NEW_JAR:-/opt/dim/pdns-output.jar}

# JRE binaries used to execute old and new jars (default to system java)
OLD_JAVA=${OLD_JAVA:-}
if [ -z "$OLD_JAVA" ]; then
    if [ -x "/opt/java8/bin/java" ] && [[ "$OLD_JAR" =~ (old|4\.) ]]; then
        OLD_JAVA="/opt/java8/bin/java"
    else
        OLD_JAVA="java"
    fi
fi
NEW_JAVA=${NEW_JAVA:-java}

OUT=${OUT:-/out/dnssec-diff}
TZS=${TZS:-UTC Europe/Berlin}
CRYPTOS=${CRYPTOS:-false true}

PDNS_DB_URI=mysql://pdns:pdns@127.0.0.1:3307/pdns1
PDNS_ADDRESS=127.1.1.1
DIM_MYSQL=(mysql -h127.0.0.1 -P3307 -udim -pdim)
PDNS_MYSQL=(mysql -h127.0.0.1 -P3307 -updns -ppdns pdns1)

ZONES="nsec.com nsec3.com nsec3salt.com parent.com child.parent.com rollover.com plain.com"

[ -f "$OLD_JAR" ] || { echo "old jar $OLD_JAR not found" >&2; exit 1; }
[ -f "$NEW_JAR" ] || { echo "new jar $NEW_JAR not found" >&2; exit 1; }
rm -rf "$OUT"
mkdir -p "$OUT"

dim_pid=
jar_pid=
cleanup() {
    [ -n "$jar_pid" ] && kill "$jar_pid" 2>/dev/null || true
    [ -n "$dim_pid" ] && kill "$dim_pid" 2>/dev/null || true
}
trap cleanup EXIT

start_dim() {
    flask run --port 5000 >>"$OUT/dim.log" 2>&1 &
    dim_pid=$!
    until bash -c 'echo > /dev/tcp/localhost/5000' 2>/dev/null; do sleep 0.5; done
    ndcli login -p p >/dev/null
}

stop_dim() {
    kill "$dim_pid"
    wait "$dim_pid" 2>/dev/null || true
    dim_pid=
}

nd() {
    # ndcli without the WARNING/INFO chatter, but failing on errors
    ndcli "$@" >/dev/null 2>"$OUT/ndcli.err" || { cat "$OUT/ndcli.err" >&2; return 1; }
}

wait_for_queue() {
    local left=unknown
    for _ in $(seq 600); do
        left=$("${DIM_MYSQL[@]}" -N dim -e 'select count(*) from outputupdate')
        [ "$left" = "0" ] && return 0
        if ! kill -0 "$jar_pid" 2>/dev/null; then
            echo "pdns-output exited, $left updates left" >&2
            return 1
        fi
        sleep 0.2
    done
    echo "pdns-output did not drain the queue ($left left)" >&2
    return 1
}

# Records every kind of name pdns-output treats specially: apex, plain rrsets,
# a wildcard, empty non-terminals and a delegation with glue (auth=0, no NSEC3).
fill_zone() {
    local z=$1 i
    nd create zone "$z"
    # subzones inherit the zone-group of their parent
    ndcli modify zone-group zg add zone "$z" >/dev/null 2>&1 || true
    nd modify zone "$z" create rr @ MX 10 mail
    nd modify zone "$z" create rr mail A 192.0.2.2
    nd modify zone "$z" create rr t A 192.0.2.1
    nd modify zone "$z" create rr t AAAA 2001:db8::1
    nd modify zone "$z" create rr txt TXT "hello world"
    nd modify zone "$z" create rr www CNAME t
    nd modify zone "$z" create rr _sip._tcp SRV 10 20 5060 t
    nd create rr "*.wild.$z." A 192.0.2.3 --only-forward
    nd modify zone "$z" create rr a.b.c A 192.0.2.4
    nd modify zone "$z" create rr sub NS ns.sub
    nd modify zone "$z" create rr ns.sub A 192.0.2.5
    for i in $(seq 1 30); do
        nd modify zone "$z" create rr "h$i" A "198.51.100.$i"
    done
}

build_fixture() {
    echo "### building the dim fixture"
    flask db clear >>"$OUT/dim.log" 2>&1
    start_dim
    nd create output out plugin pdns-db db-uri "$PDNS_DB_URI"
    nd create zone-group zg
    nd modify output out add zone-group zg

    fill_zone nsec.com
    nd modify zone nsec.com dnssec enable 8 ksk 2048 zsk 1024
    fill_zone nsec3.com
    nd modify zone nsec3.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 10
    fill_zone nsec3salt.com
    nd modify zone nsec3salt.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 100 salted
    # a DS record in a signed parent
    fill_zone parent.com
    nd modify zone parent.com dnssec enable 8 ksk 2048 zsk 1024
    fill_zone child.parent.com
    nd modify zone child.parent.com dnssec enable 8 ksk 2048 zsk 1024 nsec3 5
    # two keys of each type, as during a rollover
    fill_zone rollover.com
    nd modify zone rollover.com dnssec enable 8 ksk 2048 zsk 1024
    nd modify zone rollover.com dnssec new zsk
    nd modify zone rollover.com dnssec new ksk
    fill_zone plain.com
    stop_dim

    "${DIM_MYSQL[@]}" dim -N -e "select name, valid_begin, valid_end from zone where valid_begin is not null" \
        > "$OUT/fixture-validity.txt"
    mysqldump -h127.0.0.1 -P3307 -udim -pdim --single-transaction dim > "$OUT/fixture.sql"
    echo "$("${DIM_MYSQL[@]}" -N dim -e 'select count(*) from outputupdate') queued output updates"
}

# Changes handled incrementally by a running pdns-output. Nothing here creates
# keys or touches the validity window, so the result stays deterministic.
incremental_changes() {
    local key
    for z in nsec.com nsec3.com; do
        nd modify zone "$z" create rr new A 192.0.2.10
        nd modify zone "$z" delete rr t A 192.0.2.1
        nd modify zone "$z" create rr txt TXT "second string"
        nd modify zone "$z" delete rr '*.wild' A 192.0.2.3
        nd modify zone "$z" create rr x.y.z A 192.0.2.11
    done
    # removing a delegation turns glue into authoritative data
    nd modify zone nsec3.com delete rr sub NS ns.sub
    nd modify zone nsec3.com delete rr ns.sub A 192.0.2.5
    # finish the rollover: drop the older zsk and ksk
    for type in ZSK KSK; do
        key=$(ndcli list zone rollover.com keys 2>/dev/null | awk -v t=$type '$2 == t {print $1}' | sort | head -1)
        nd modify zone rollover.com dnssec delete key "$key"
    done
    # removes the DS from parent.com
    nd modify zone child.parent.com dnssec disable
    nd modify zone nsec3salt.com dnssec disable
    nd modify zone plain.com create rr new A 192.0.2.12
}

dump_pdns() {
    local prefix=$1
    "${PDNS_MYSQL[@]}" -N -B -e "
        select d.name, r.name, r.type, r.content, r.ttl, r.prio, r.disabled,
               coalesce(r.ordername, 'NULL'), r.auth
        from records r join domains d on d.id = r.domain_id" | LC_ALL=C sort > "$prefix.records"
    "${PDNS_MYSQL[@]}" -N -B -e "
        select d.name, d.type, coalesce(d.master, ''), coalesce(d.account, '') from domains d" \
        | LC_ALL=C sort > "$prefix.domains"
    "${PDNS_MYSQL[@]}" -N -B -e "
        select d.name, m.kind, m.content from domainmetadata m join domains d on d.id = m.domain_id" \
        | LC_ALL=C sort > "$prefix.domainmetadata"
    "${PDNS_MYSQL[@]}" -N -B -e "
        select d.name, k.flags, k.active, k.content from cryptokeys k join domains d on d.id = k.domain_id" \
        | LC_ALL=C sort > "$prefix.cryptokeys"
}

# axfr every signed zone from pdns and validate it with jdnssec-verifyzone
verify_served_zones() {
    local dir=$1 z rc=0
    for z in $ZONES; do
        dig +onesoa +noall +answer axfr "$z" "@$PDNS_ADDRESS" > "$dir/$z.axfr"
        grep -q RRSIG "$dir/$z.axfr" || continue
        if "$CANDIDATE_JAVA" -cp "$CANDIDATE_JAR" com.verisignlabs.dnssec.cl.VerifyZone "$dir/$z.axfr" \
                > "$dir/$z.verify" 2>&1; then
            echo "  verified  $z"
        else
            echo "  INVALID   $z (see $dir/$z.verify)"
            rc=1
        fi
    done
    return $rc
}

# $1 label, $2 java, $3 jar, $4 timezone, $5 useNativeCrypto
run_jar() {
    local label=$1 java=$2 jar=$3 tz=$4 native=$5
    local dir="$OUT/runs/$label/${tz//\//_}/native-$native"
    mkdir -p "$dir"
    echo "### $label jar, TZ=$tz, useNativeCrypto=$native"

    "${DIM_MYSQL[@]}" dim < "$OUT/fixture.sql"
    "${PDNS_MYSQL[@]}" -e 'delete from records; delete from domains; delete from domainmetadata; delete from cryptokeys'
    { grep -v '^useNativeCrypto=' tests/pdns-output-test.properties; echo "useNativeCrypto=$native"; } \
        > "$dir/pdns-output.properties"

    start_dim
    "$java" -Duser.timezone="$tz" -jar "$jar" --config "$dir/pdns-output.properties" \
        > "$dir/pdns-output.log" 2>&1 &
    jar_pid=$!

    wait_for_queue
    dump_pdns "$dir/1-initial"
    if grep -q libgmp "/proc/$jar_pid/maps"; then echo yes; else echo no; fi > "$dir/libgmp-loaded"
    verify_served_zones "$dir" > "$dir/1-initial.verify" || true

    incremental_changes
    wait_for_queue
    dump_pdns "$dir/2-incremental"
    verify_served_zones "$dir" > "$dir/2-incremental.verify" || true

    kill "$jar_pid"
    wait "$jar_pid" 2>/dev/null || true
    jar_pid=
    stop_dim

    cat "$dir/1-initial.verify" "$dir/2-incremental.verify" | sort | uniq -c | sed 's/^/  /'
    echo "  libgmp loaded: $(cat "$dir/libgmp-loaded")"
    grep -q INVALID "$dir/1-initial.verify" "$dir/2-incremental.verify" && return 1 || return 0
}

build_fixture

rc=0
for tz in $TZS; do
    for native in $CRYPTOS; do
        run_jar old "$OLD_JAVA" "$OLD_JAR" "$tz" "$native" || rc=1
        run_jar new "$NEW_JAVA" "$NEW_JAR" "$tz" "$native" || rc=1
    done
done

echo
echo "################ old vs new"
for tz in $TZS; do
    for native in $CRYPTOS; do
        sub="${tz//\//_}/native-$native"
        for f in "$OUT/runs/old/$sub"/*.{records,domains,domainmetadata,cryptokeys}; do
            name=$(basename "$f")
            if diff -u "$f" "$OUT/runs/new/$sub/$name" > "$OUT/runs/new/$sub/$name.diff"; then
                rm "$OUT/runs/new/$sub/$name.diff"
                printf '  same      %-40s %s\n' "$sub" "$name"
            else
                printf '  DIFFERENT %-40s %s (%s lines)\n' "$sub" "$name" \
                    "$(grep -c '^[-+][^-+]' "$OUT/runs/new/$sub/$name.diff")"
                rc=1
            fi
        done
    done
done

echo
echo "################ java crypto vs native crypto (new jar)"
for tz in $TZS; do
    a="$OUT/runs/new/${tz//\//_}/native-false"
    b="$OUT/runs/new/${tz//\//_}/native-true"
    [ -d "$a" ] && [ -d "$b" ] || continue
    for name in 1-initial.records 2-incremental.records; do
        if cmp -s "$a/$name" "$b/$name"; then
            printf '  same      %-20s %s\n' "$tz" "$name"
        else
            printf '  DIFFERENT %-20s %s\n' "$tz" "$name"
            rc=1
        fi
    done
done

echo
[ $rc -eq 0 ] && echo "### no behaviour change" || echo "### differences found, see $OUT"
exit $rc
