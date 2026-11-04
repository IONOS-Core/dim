# dim-testsuite

This is the test suite to run tests over all the components of dim, including
ndcli, dimclient, dim itself, the output and some more.

## Local Testbed & Quickstart (Recommended)

From the repository root, you can manage the local testbed and run test suites using `make`:

```text
+-------------------------------------------------------------------------------+
|               HOST WORKSTATION (Repository Root: ~/git/.../dim)               |
|                                                                               |
|   make testbed-start | make testbed-shell | make testbed-logs | make check    |
+-------------------------------------------------------------------------------+
             │ (live volume mounts & port bindings)
             ▼
+-------------------------------------------------------------------------------+
|                  SHARED DOCKER NETWORK (service:mariadb)                     |
|                                                                               |
|  +---------------------------------------------------------------+            |
|  | dim-mw (DIM Application Server & Interactive Shell)           |            |
|  | - Flask API Server (Port 5000)                                |            |
|  | - make testbed-shell: ndcli (auto-login), dig (@127.1.1.1)    |            |
|  | - Live mounts: ./dim, ./dimclient, ./ndcli                    |            |
|  | - Testing: make test, pytest                                  |            |
|  +---------------------------------------------------------------+            |
|            │                                        │                         |
|         DNS:53                                   SQL:3307                     |
|            ▼                                        ▼                         |
|  +------------------------+             +------------------------+            |
|  | pdns1 (Auth NS 1)      |◄──SQL:3307──| mariadb (Port 3307)    |            |
|  | - 127.1.1.1:53 (UDP)   |             | - dim database         |            |
|  | - pdns1 database       |             | - pdns1 & pdns2 tables |            |
|  +------------------------+             +------------------------+            |
|            ▲                                        │                         |
|         Sync:SQL                                 Poll:SQL                     |
|            │                                        ▼                         |
|  +---------------------------------------------------------------+            |
|  | dim-output (pdns-output daemon)                               |            |
|  | - Java 21 DNSSEC signer & PowerDNS synchronizer               |            |
|  | - Live logs via make testbed-logs                             |            |
|  +---------------------------------------------------------------+            |
+-------------------------------------------------------------------------------+
```

```shell
make help           # Show all available commands with tab completion
make testbed-start  # Start MariaDB, PowerDNS 1 & 2, dim-mw, and dim-output in background (fast, 1s)
make testbed-build  # Rebuild testbed container images from scratch (only on Dockerfile/dep changes)
make testbed-shell  # Open interactive shell (ndcli pre-authenticated, tab-completion, dig @127.1.1.1)
make testbed-seed   # Populate testbed with demo zone (test.example.com) and sample records
make testbed-test   # Run E2E CLI scenario tests (t/*.t) against testbed
make testbed-logs   # Follow live logs from middleware and pdns-output daemon
make shell-pdns     # Open PowerDNS authoritative nameserver shell
make restart-mw     # Fast-reload dim-mw container after editing Python code (1s)
make restart-output # Fast-reload dim-output container after editing Java code (1s)
make docs           # Build Sphinx HTML documentation inside container
make docs-serve     # Build and serve HTML docs on http://localhost:8080
make test           # Run pytest unit/integration test suite (fast, ~15s)
make check          # Run syntax validation and containerized unit tests
make testbed-stop   # Stop and tear down all testbed containers
```

#### When to use `testbed-start` vs. `testbed-build`:
- **`make testbed-start` (Day-to-day development):** Starts existing containers in background in **1–2 seconds**. Local Python code (`dim/`, `ndcli/`, `dimclient/`) is live-mounted into the containers and immediately active without rebuilding.
- **`make testbed-build` (Infrequent):** Rebuilds container images from scratch (takes ~3 minutes). Only needed when `Dockerfile`, system packages (`apt-get`), or build dependencies change.

> **Full Regression Test Suite:**
> To run the complete 250+ test suite locally (warning: takes several minutes), run:
> `docker compose -f dim-testsuite/compose.yaml run --rm testsuite all`
> Normally, developers should let GitHub Actions CI run the full suite automatically on PR push.

### Running Individual Tests
- **Python Pytest (Unit / Integration Tests):**
  - From host: `make test` (or `make test PYTESTS="update_validity"`)
  - Inside `make testbed-shell`: `pytest tests/update_validity_test.py`
- **CLI / Scenario Tests (E2E Tests in `t/*.t`):**
  - From host: `make testbed-test` (or `make testbed-test TESTS="zone-dnssec-attrs.t"`)
  - Inside `make testbed-shell`: `make test TESTS=zone-dnssec-attrs.t`

### Safety Guarantee: Production DIM is Never Touched
* The testbed runs in a completely isolated Docker network namespace.
* `NDCLI_SERVER=http://localhost:5000` is enforced; personal credentials in `~/.ndclirc` are ignored.
* `dig` defaults to querying the local PowerDNS instance (`@127.1.1.1`).
* Your local Git checkout is live-mounted into the container, so edits to Python or template files take effect immediately without rebuilding images.
* To reload the DIM API after code changes: `make restart-mw` (takes ~1s).

## running in containers directly

`compose.yaml` brings up the whole environment (mariadb, two pdns instances, a
freshly built `pdns-output` jar and a virtualenv with `dim`, `dimclient` and
`ndcli`) and runs both suites:

```shell
docker compose up --build --abort-on-container-exit --exit-code-from testsuite
```

The `testsuite` service takes one argument selecting what to run: `all`
(default, pytest suite followed by the ndcli suite), `pytest`, `ndcli`,
`dnssec` (every dnssec related test, plus signing zones and validating them
with drill and jdnssec-verifyzone), `dnssec-diff` (see below) or `shell` for an
interactive prompt:

```shell
docker compose run --rm testsuite pytest
docker compose run --rm testsuite shell
```

The `TESTS` and `PYTESTS` variables described below work here too:

```shell
docker compose run --rm -e TESTS="zone-show-1.t" testsuite ndcli
docker compose run --rm -e PYTESTS="AllocatorTest" testsuite pytest
```

The suite hardcodes `127.0.0.1:3307` for mariadb and `127.1.1.1` / `127.2.2.2`
for the two pdns instances, so every service shares mariadb's network
namespace. mariadb's datadir is a tmpfs, so each `docker compose up` starts
from an empty database.

The container copies the checkout in at build time; rerun with `--build` after
changing `dim/`, `ndcli/`, `dimclient/`, `dim-testsuite/` or `pdns-output/`.

### comparing pdns-output against a previous build

`dnssec-diff` runs a previous `pdns-output` jar (the old version) and the newly built
jar from the checkout over the same dim database and diffs the resulting PowerDNS
databases. Keys and validity windows come from one fixture and RSASHA256
signatures are deterministic, so an unchanged behaviour gives identical
records, RRSIGs included. Each jar runs per JVM timezone (`TZS`, default
`UTC Europe/Berlin`) and with and without `useNativeCrypto` (`CRYPTOS`).

Put the old jar into `baseline/pdns-output-old.jar`:

```shell
docker compose run --rm -v ./baseline:/opt/baseline:ro -v ./out:/out testsuite dnssec-diff
```

You can customize the jars and Java runtimes via environment variables:
`OLD_JAR`, `NEW_JAR`, `OLD_JAVA`, `NEW_JAVA`.

Dumps, diffs and logs end up in `out/dnssec-diff/`.

## running on a local machine

As of now, make sure that your home *does not* contain a vaild `.ndclirc` and `.ndcli.cookie`.
If ndcli finds valid login information these might get use. In consequence the tests will be
run on the production DIM.

## requirements

You need a running mysql instance listening on 127.0.0.1:3307 with the following databases:

```sql
create database dim;
create database pdns1;
create database pdns2;
```

and the following users:

```sql
grant all on pdns1.* to 'pdns1'@'localhost' identified by 'pdns';
grant all on pdns2.* to 'pdns2'@'localhost' identified by 'pdns';
grant all on dim.* to 'dim'@'localhost' identified by 'dim';
```

You also need to grant the user running the tests full rights on the databases:

```sql
create user 'janedoe'@'localhost';
grant all on pdns1.* to 'janedoe'@'localhost';
grant all on pdns2.* to 'janedoe'@'localhost';
grant all on dim.* to 'janedoe'@'localhost';
```

If MariaDB does not startup please try to disable SELinux and try again.

The database files should be on a tmpfs to have a bit better performance for testing.

Then create the file `/etc/dim/dim.cfg` with a production like configuration.
It must contain the database credentials, so that the server can connect to the database.

Example config:

```ini
### Database
SQLALCHEMY_DATABASE_URI = 'mysql://dim:dim@127.0.0.1:3307/dim'
DB_LOCK_TIMEOUT = 120

# Set SECRET_KEY to a random string
# The security of this application is compromised if SECRET_KEY is leaked
SECRET_KEY = 'testkey'
PERMANENT_SESSION_LIFETIME = 30 * 24 * 3600 # 1 month
TEMPORARY_SESSION_LIFETIME = 24 * 3600            # 1 day


### Logging
import logging, logging.handlers, sys
LOGGING_LEVEL = logging.DEBUG
LOGGING_HANDLER = logging.StreamHandler(sys.stderr)
#LOGGING_HANDLER = logging.handlers.SysLogHandler(address='/dev/log', facility='local0')


### DNS
DNS_DEFAULT_REFRESH  = 14400  # 4 hours
DNS_DEFAULT_RETRY    = 3600   # 1 hour
DNS_DEFAULT_EXPIRE   = 605000 # 7 days
DNS_DEFAULT_MINIMUM  = 86400  # 1 day (Min. TTL)
DNS_DEFAULT_ZONE_TTL = 86400  # 1 day (Default. TTL)

# list of ipspaces which are allowed to exist multiple times in dim (layer3domains)
# in general only rfc1918 ip should be allowed
LAYER3DOMAIN_WHITELIST = ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '100.64.0.0/10']


### Debugging
SQLALCHEMY_DATABASE_URI_TEST = 'mysql://dim:dim@127.0.0.1:3307/dim'
PDNS_DATABASE_URI_TEST = 'mysql://pdns1:pdns@127.0.0.1:3307/pdns1'
SYNC_PDNS_OUTPUT = False
DEBUG = False
SQLALCHEMY_LOG = False
```

The following programs need to be installed:

* python3-devel
* colordiff
* make
* mariadb-devel
* openldap-devel
* gcc

### ndcli test suite

To run the ndcli test suite, make sure the mysql database is up and running.

Then call `make`.
It will proceed and build a virtual environment and install the tools needed
into it, and initialize the databases.

After that it will start the dim server and start making ndcli calls against
the server.

To control the location of the virtual environment, the output directory, or
the log directory, use the variables `VDIR`, `ODIR` and `LDIR`.

```shell
make VDIR=/tmp/venv ODIR=/tmp/test_output LDIR=/tmp/test_logs
```

If you want to skip the database initialization or installing of dependencies,
let *make* run the tests directly by using `make test`.

To run only specific testfiles, use the variable `TESTS` for `make test` after
you've created the virtual environment.

```shell
make test TESTS="<test1> <test2>"
```

`runtest.py` will look in the `t` directory for the specific testcases and run
only the ones specified in `TESTS`. After the case was run, your dim environment will have
exactly the state it broke in, so you can poke into it with ndcli, for example:

```shell
$VDIR/bin/ndcli list pools
```

### pytest test suite

To run the pytest test suite, make sure the mysql database is up and running.

Then call `make install`, or `make install-dev` (editable setup, changes to the code tree
will affect the virtual environment after creation).
It will proceed and build a virtual environment and install the tools needed
into it.

If you did not initialize the databases yet, run `make db` to do so.

To run the pytests, call `make pytest`.

To run only specific pytests, use the variable `PYTESTS` for `make pytest` after
you've created the virtual environment.

```shell
make pytest PYTESTS="<test1> <test2>"
```

This will run only the pytest(s) you specified.
For this, you'll need to pass the test *class names*, not the filenames:
e.g. `make pytest PYTESTS="AllocatorTest"` to run the tests for that class,
which is from the `tests/allocator_test.py` file.
