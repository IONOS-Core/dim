# Contributing to DIM

Thank you for you interest to contribute to the DIM project. This document
will explain some of the things you need to keep in mind when contributing to
ease the workflow of this.

## Filing an Issue or Bug

When filing an issue or bug report, make the title of the issue a very short
summary (e.g. "Allow setting IPblock status"). In the
content of the issue, be as detailed as possible. Supply at least the following
information:

* Steps to reproduce: How can we reproduce the issue
* Expected behavior: what did you expect what would happen?
* Observed behavior: what actually happened when following the steps?

If you have already looked deeper into the problem, provide what you found as
well.

## Filing a Pull Request

Code contributions are sent as a pull request on GitHub. By submitting a
Pull Request you agree to your code becoming MIT licensed.

## Pull Request Guidelines

A pull request, at the least, should have:

* A clear and concise title (not e.g. 'Issue #1234')
* A description of the patch (what issue does it solve or what
feature does it add)
* Documentation for the feature or when current behavior changes

And must:

* Be filed against the master branch
* Be a branch with a meaningful name in the forked repository
* Have one or more testcases in `dim-testsuite/t` (for CLI / ndcli changes) or `dim-testsuite/e2e/suites/` (for full-stack E2E behavior)

## Contributor Workflow & Development Setup

### 1. Initial Setup
Install development tools (`darker`, `ruff`) and enable local Git pre-commit hooks:
```sh
make install-dev
```

### 2. Testing
DIM uses multi-tiered testing:
- **Fast Linting:** `make lint` (validates modified files via `shellcheck`, `darker`, `ruff`)
- **Unit & Middleware Tests:** `make test-dim` and `make test-ndcli`
- **CLI Scenario Tests:** `make test-scenarios` (runs `dim-testsuite/t/*.t` in clean container)
- **Live Stack E2E Tests:** `make test-e2e` (runs live against MariaDB, PowerDNS, Flask, pdns-output)
  - Run specific domain filter: `make test-e2e SUITE=dnssec` (or `SUITE=ipam`, `SUITE=records`)

**Testing Guidelines & Rules:**
- Tests that verify DIM middleware, CLI, IPAM, zone, or record behavior WITHOUT requiring live authoritative nameserver (`dig`) queries MUST be written as standard `.t` scenario tests in `dim-testsuite/t/`.
- Before writing any new tests, contributors MUST inspect existing `.t` tests in `dim-testsuite/t/` and Python tests in `dim-testsuite/tests/` to avoid creating duplicate test cases.
- Live stack E2E suites (`dim-testsuite/e2e/suites/`) are reserved strictly for multi-daemon interaction requiring DNSSEC signing, PowerDNS publication, and `dig` verification against nameservers.
- In all tests (both `.t` and E2E scripts): **DO NOT use variable indirections** (e.g., `BLAFASEL=10.2.215.0/24; ndcli modify pool p add subnet $BLAFASEL`). Always write explicit literal values directly into commands so tests are readable, debuggable, and copy-pasteable.

### 3. Local Testbed
Start and explore the isolated local Docker testbed:
```sh
make testbed-start   # Start MariaDB, PowerDNS, dim-mw, dim-output (1s)
make testbed-shell   # Drop into interactive shell with ndcli and dig @127.1.1.1
make testbed-status  # Check container health
make testbed-stop    # Clean up containers
```

### 4. Code & CLI Conventions
- Use **long-format options** in scripts and documentation (e.g. `--cleanup` over `-c`, `--dryrun` over `-n`, `--quiet` over `-q`).
- Code must pass `make lint` before submitting a PR.
- Use Conventional Commits (`feat:`, `fix:`, `test:`, `chore:`).
