#!/bin/bash
#
# Runs the test suites inside the container. See dim-testsuite/compose.yaml.
#
# Usage: entrypoint.sh [all|pytest|ndcli|dnssec|dnssec-diff|shell]
#
set -eu

cd /src/dim-testsuite

export PATH="/src/dim-testsuite/virtual_env/bin:$PATH"
# FLASK_APP is not exported on purpose: the Makefile passes its own (quoted)
# value to the shell, an exported one would override it and break quoting

wait_for() {
    local what=$1 tries=60
    while ! eval "$2" >/dev/null 2>&1; do
        tries=$((tries - 1))
        if [ $tries -le 0 ]; then
            echo "timed out waiting for $what" >&2
            return 1
        fi
        sleep 1
    done
}

wait_for mariadb "mysql -h127.0.0.1 -P3307 -udim -pdim -e 'select 1' dim"
# the pdns instances only answer once they have their schema
wait_for pdns1 "dig +time=1 +tries=1 @127.1.1.1 localhost soa"
wait_for pdns2 "dig +time=1 +tries=1 @127.2.2.2 localhost soa"

run_pytest() {
    echo "### initializing the dim database"
    make db-clear
    echo "### running the pytest suite"
    if [ -n "${PYTESTS:-}" ]; then
        # not "make pytest": the Makefile passes -k unquoted, which breaks
        # expressions like "A or B"
        virtual_env/bin/pytest -k "$PYTESTS"
    else
        virtual_env/bin/pytest
    fi
}

run_ndcli() {
    echo "### running the ndcli suite"
    # TESTS empty means all tests in t/
    # runtest.py reads ndcli's stdout and stderr from one pipe and the expected
    # outputs in t/ rely on stdout being block buffered (flushed at exit, after
    # the log messages on stderr)
    env -u PYTHONUNBUFFERED make test TESTS="${TESTS:-}"
}

# everything dnssec: the unit and rpc tests, the zone signing done by
# pdns-output (tests/dnssec_test.py) and the ndcli tests that validate signed
# zones served by pdns
run_dnssec() {
    rc=0
    echo "### initializing the dim database"
    make db-clear
    echo "### running the dnssec pytests"
    virtual_env/bin/pytest -k "DNSKEYTest or NSEC3ParamsTest or ZoneDnssecTest or PdnsOutputDnssecTest" || rc=1
    echo "### running the dnssec tests from t/"
    # one line: make passes TESTS to a shell, where a newline ends the command
    local tests="zone-dnssec-attrs.t zone-dnssec-delete.t zone-dnssec-subzone-1.t"
    tests+=" zone-dnssec-subzone-2.t zone-dnssec-subzone-3.t zone-dnssec-subzone-4.t"
    tests+=" zone-nsec3-attrs.t history-zone-dnssec.t rr-create-ds-1.t"
    env -u PYTHONUNBUFFERED make test TESTS="$tests" || rc=1
    echo "### signing and validating zones with drill and jdnssec-verifyzone"
    docker/dnssec-check.sh || rc=1
    return $rc
}

case "${1:-all}" in
    all)
        # run both suites even if the first one fails
        rc=0
        run_pytest || rc=1
        run_ndcli || rc=1
        exit $rc
        ;;
    pytest)
        run_pytest
        ;;
    ndcli)
        run_ndcli
        ;;
    dnssec)
        run_dnssec
        ;;
    dnssec-diff)
        exec docker/dnssec-diff.sh
        ;;
    shell)
        exec /bin/bash
        ;;
    *)
        exec "$@"
        ;;
esac
