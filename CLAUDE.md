# DIM - DNS and IP Management (and also DHCP)

## Overview
DIM is the central DNS and IP Management (and DHCP) system developed by IONOS. It manages IPv4 and IPv6 address spaces, forward and reverse DNS zones, and DNSSEC keys, synchronizing authoritative zone records to PowerDNS databases via the `pdns-output` daemon.

> ⚠️ **MANDATORY FOR ALL CONTRIBUTORS:**
> 1. **Read [CONTRIBUTING.md](CONTRIBUTING.md)** thoroughly before opening PRs or contributing code (covers contribution rules, MIT licensing, and PR standards).
> 2. **Multi-Tier Testing Mandate:** Every code change (feature or bug fix) **MUST** provide tests at **both** levels:
>    * **Component / Software Level:** Focused unit or integration tests for the individual component being modified (e.g., Pytest in `dim-testsuite/tests/` for middleware, unit tests in `ndcli/tests/` for CLI, Gradle tests in `pdns-output/` for Java).
>    * **End-to-End (E2E) Level:** Full scenario tests (in `dim-testsuite/t/*.t` for CLI operations or `dim-testsuite/e2e/*.sh` for automated multi-daemon DNSSEC lifecycles) verifying system-wide behavior across the entire stack.

## Architecture & Philosophy: Local Testbed vs. CI Pipelines

DIM strictly separates the **Local Testbed** (Inner Dev-Loop) from the **CI Pipelines** (Outer Quality Gate):

### 1. Local Testbed (Inner Dev-Loop — Speed & Instant Feedback)
* **Goal:** Maximum iteration velocity for developers without touching production or staging systems.
* **Live-Mounts:** Local Python code (`dim/`, `dimclient/`, `ndcli/`) is live-mounted directly into containers. Edits take effect immediately.
* **Fast Reloading:**
  * `make restart-mw`: Reloads DIM Flask middleware in ~1s.
  * `make restart-output`: Incrementally compiles Java code and reloads pdns-output daemon in ~4s.
* **Interactive Exploration:**
  * `make testbed-shell`: Drops into `dim-mw` with pre-authenticated `ndcli` (tab-completion enabled), `dig` defaulting to `@127.1.1.1`, and ability to run E2E scenarios (`bash e2e/dnssec-user-ops.sh`).
* **Selective Testing:** Run fast, targeted tests against the active testbed (`make test-dim PYTESTS="dnssec"` or `make test-e2e TESTS="zone-dnssec-attrs.t"`).

### 2. CI Pipelines (Outer Quality Gate — Hermetic Production Simulation)
* **Goal:** Rigorously verify that the entire codebase builds, initializes, and passes all tests from a clean state under realistic enterprise environments (Oracle Linux 9, MariaDB, PowerDNS, JDK 21).
* **Workflows (`.github/workflows/`):**
  * `test_ndcli.yml`: ndcli unit tests in a clean Oracle Linux 9 container.
  * `test_dim.yml`: DIM middleware pytests with a clean MariaDB service container.
  * `test_pdns_output.yml`: pdns-output Gradle suite with JDK 21, MariaDB, and PowerDNS.
  * `test_e2e.yml`: Full regression testbed runner executing all 203 scenario tests (`t/*.t`) and all shell scenarios (`dim-testsuite/e2e/*.sh`).

## Architecture Components
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
make check          # Run syntax validation and fast unit test suites (DIM + ndcli)
make lint           # Check Python and shell script syntax
make test-dim       # Run DIM middleware pytest suite in container (optional: PYTESTS="expression")
make test-ndcli     # Run ndcli CLI unit tests (cliparse_test.py)
make test-pdns-output # Run pdns-output Java test suite
make test-e2e       # Run E2E CLI scenario tests (t/*.t) & e2e/*.sh in container
make test-all       # Run all test suites across all components
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
make testbed-status  # Show health and status of all testbed services
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
  - From host: `make test-dim` (or `make test-dim PYTESTS="update_validity"`)
  - Inside `make testbed-shell`: `pytest tests/update_validity_test.py`
- **CLI / Scenario Tests (E2E Tests in `t/*.t`):**
  - From host: `make test-e2e` (or `make test-e2e TESTS="zone-dnssec-attrs.t"`) — *requires clean environment; aborts if testbed is running (`make testbed-stop`)*
  - Inside `make testbed-shell`: `make test TESTS=zone-dnssec-attrs.t`
- **ndcli CLI Unit Tests:**
  - From host: `make test-ndcli`
- **pdns-output Tests:**
  - From host: `make test-pdns-output` (requires active testbed)

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
