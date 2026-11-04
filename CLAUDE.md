# DIM - DNS and IP Management (and also DHCP)

## Overview
DIM is the central DNS and IP Management (and DHCP) system developed by IONOS. It manages IPv4 and IPv6 address spaces, forward and reverse DNS zones, and DNSSEC keys, synchronizing authoritative zone records to PowerDNS databases via the `pdns-output` daemon.

Before contributing code or opening pull requests, please read [CONTRIBUTING.md](CONTRIBUTING.md) for contribution rules, MIT licensing terms, and pull request guidelines.

## Architecture
- `dim/`: Core Python middleware (Flask REST and JSON-RPC API, SQLAlchemy models, MySQL/MariaDB database).
- `dimclient/`: Python client library for talking to the DIM API.
- `ndcli/`: Command-line interface for administrators and automation.
- `pdns-output/`: Java daemon that polls DIM `outputupdate` table, signs zones with DNSSEC, and synchronizes records into PowerDNS databases.
- `dim-testsuite/`: Containerized testbed and test suites (MariaDB, PowerDNS 1 & 2, pytest suites, CLI integration tests).

## Testbed Architecture Diagram

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

## Development & Testbed Workflow

The root `Makefile` provides a self-documenting workflow (press `<Tab>` or run `make help`):

```shell
make help           # Show available commands with descriptions
make check          # Run syntax validation and fast pytest suite
make lint           # Check Python and shell script syntax
make test           # Run pytest unit/integration test suite (fast, ~15s)
make java-test      # Run pdns-output Java test suite (requires testbed-start)
make testbed-test   # Run E2E CLI scenario tests (t/*.t) in container
make docs           # Build Sphinx HTML documentation inside container
make docs-serve     # Build and serve HTML docs on http://localhost:8080
```

### Local Testbed (Recommended for Live Testing)
The local testbed starts dedicated containers matched to real production server roles:
1. `mariadb`: Test database on port 3307
2. `pdns1` & `pdns2`: PowerDNS authoritative nameservers on port 53
3. `dim-mw`: DIM middleware application server (runs Flask API, pre-configured with output & zone group)
4. `dim-output`: Output sync daemon (runs `pdns-output.jar`)

```shell
make testbed-start   # Start MariaDB, PowerDNS, dim-mw, and dim-output in background (fast, 1s)
make testbed-build   # Rebuild testbed container images from scratch (only on Dockerfile/dep changes)
make testbed-shell   # Open interactive shell (pre-authenticated ndcli, tab-completion, dig @127.1.1.1)
make testbed-seed    # Populate testbed with demo zone (test.example.com) and sample records
make testbed-test    # Run E2E scenario tests against containerized testbed
make testbed-logs    # Follow live logs from middleware and pdns-output daemon
make shell-pdns      # Open PowerDNS authoritative nameserver shell
make restart-mw      # Fast-reload dim-mw container after editing Python code (1s)
make restart-output  # Fast-reload dim-output container after editing Java code (1s)
make testbed-stop    # Stop and clean up testbed containers
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

### Shell Environments & Log Locations
| Command | Container | Role | CLI Tools | Log Location |
| :--- | :--- | :--- | :--- | :--- |
| `make testbed-shell` | `dim-mw` | Interactive Testbed Shell | `ndcli` (tab-complete), `dig`, `pytest`, `make test` | Interactive / `make testbed-logs` |
| `make testbed-logs` | `dim-mw`, `dim-output` | Live Logs | `docker compose logs -f` | Container stdout |
| `make shell-pdns` | `pdns1` | Nameserver | `pdns_control`, `mysql` | Container stdout |

### Safety Guarantee: Production DIM is Never Touched
- `make testbed-shell` sets `NDCLI_SERVER=http://localhost:5000` and `dig` defaults to `@127.1.1.1`.
- Production credentials in `~/.ndclirc` are **never** used.
- Local repository files are live-mounted into containers with `PYTHONPATH` precedence so code edits take effect immediately without rebuilding Docker images.

## Code & Git Conventions
- Python code requires Python 3.9+ compatibility.
- Branch naming: `feature/<name>`, `fix/<name>`, or `chore/<name>`.
- Commits: Use Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`) with clear explanations.
- Never commit internal hostnames (*.server.lan), internal IPs, or internal credentials to this public repository.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for PR submission standards and MIT licensing agreement.
