#!/bin/bash
#
# Runs the test suites or services inside the container. See dim-testsuite/compose.yaml.
#
# Usage: entrypoint.sh [all|pytest|ndcli|dnssec|dnssec-diff|shell|mw|output]
#
set -eu

cd /src/dim-testsuite

# Install dig wrapper so DNS queries in the testbed default to local PowerDNS (127.1.1.1)
if [ -f /src/dim-testsuite/docker/dig-wrapper.sh ] && [ ! -L /usr/local/bin/dig ]; then
    ln -sf /src/dim-testsuite/docker/dig-wrapper.sh /usr/local/bin/dig
fi

# Ensure ndcli, manage_dim, manage_db, and pytest are in PATH and /opt/dim/bin
mkdir -p /opt/dim/bin
for bin in ndcli manage_dim manage_db pytest; do
    if [ -f "/src/dim-testsuite/virtual_env/bin/$bin" ]; then
        ln -sf "/src/dim-testsuite/virtual_env/bin/$bin" "/usr/local/bin/$bin"
        ln -sf "/src/dim-testsuite/virtual_env/bin/$bin" "/opt/dim/bin/$bin"
    fi
done

# Ensure colored aliases are active in all interactive and non-interactive bash shells
for rc in /etc/bash.bashrc /root/.bashrc; do
    if ! grep -q "alias ll=" "$rc" 2>/dev/null; then
        echo 'alias ls="ls --color=auto"' >> "$rc"
        echo 'alias ll="ls -lh --color=auto"' >> "$rc"
        echo 'alias la="ls -lha --color=auto"' >> "$rc"
    fi
done

export PATH="/usr/local/bin:/src/dim-testsuite/virtual_env/bin:$PATH"
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
        if [ "$#" -gt 1 ]; then
            exec /src/dim-testsuite/virtual_env/bin/ndcli "${@:2}"
        else
            run_ndcli
        fi
        ;;
    dnssec)
        run_dnssec
        ;;
    dnssec-diff)
        exec docker/dnssec-diff.sh
        ;;
    shell)
        exec /src/dim-testsuite/docker/testbed-shell.sh
        ;;
    mw)
        if ! mysql -h127.0.0.1 -P3307 -udim -pdim -e 'select 1 from user limit 1' dim >/dev/null 2>&1; then
            echo "### initializing fresh dim and powerdns databases"
            make db

            echo "### starting dim middleware API on port 5000"
            fuser -k 5000/tcp >/dev/null 2>&1 || true
            sleep 0.5
            FLASK_APP='dim:create_app(db_mode="TEST")' flask run --host=0.0.0.0 --port=5000 &
            FLASK_PID=$!

            wait_for "DIM Middleware API" "echo > /dev/tcp/localhost/5000"

            echo "### configuring default output and zone-group"
            ndcli login -p p >/dev/null 2>&1 || true
            ndcli create output out plugin pdns-db db-uri "mysql://pdns:pdns@127.0.0.1:3307/pdns1" >/dev/null 2>&1 || true
            ndcli create zone-group zg >/dev/null 2>&1 || true
            ndcli modify output out add zone-group zg >/dev/null 2>&1 || true
        else
            echo "### starting dim middleware API on port 5000 (existing database)"
            fuser -k 5000/tcp >/dev/null 2>&1 || true
            sleep 0.5
            FLASK_APP='dim:create_app(db_mode="TEST")' flask run --host=0.0.0.0 --port=5000 &
            FLASK_PID=$!
            wait_for "DIM Middleware API" "echo > /dev/tcp/localhost/5000"
        fi

        echo "[OK] DIM Middleware is ready on http://localhost:5000"
        trap 'kill -TERM $FLASK_PID 2>/dev/null; wait $FLASK_PID' SIGTERM SIGINT
        wait $FLASK_PID
        ;;
    output)
        wait_for "DIM Middleware API" "echo > /dev/tcp/localhost/5000"
        echo "### starting pdns-output daemon"
        exec java -jar /opt/dim/pdns-output.jar --config tests/pdns-output-test.properties
        ;;
    *)
        exec "$@"
        ;;
esac
