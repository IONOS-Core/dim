.PHONY: help check lint test-dim test-pdns-output test-ndcli test-e2e test-all docs docs-serve testbed-start testbed-build testbed-stop testbed-status testbed-seed testbed-shell testbed-logs shell-output shell-pdns restart-mw restart-output clean
.DEFAULT_GOAL := help

COMPOSE ?= docker compose -f dim-testsuite/compose.yaml
PYTHON  ?= python3

help:  ## Show this help message
	@awk 'BEGIN {FS = ":.*?## "} \
		/^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } \
		/^[a-zA-Z0-9_-]+:.*?## / { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

##@ Code Quality & Verification

check: lint test-dim test-ndcli  ## Run syntax validation and fast unit test suites

lint:  ## Check Python and shell script syntax
	@echo "==> Checking Python syntax..."
	@$(PYTHON) -m py_compile dim/dim/models/*.py dim/dim/*.py ndcli/dimcli/*.py
	@echo "==> Checking shell script syntax..."
	@bash -n dim-testsuite/docker/*.sh dim-testsuite/e2e/*.sh
	@echo "[OK] Syntax checks passed!"

test-dim:  ## Run DIM middleware pytest suite in container (optional: PYTESTS="expression")
	@echo "==> Running DIM middleware pytest suite inside container..."
	@$(COMPOSE) run --rm -e PYTESTS="$(PYTESTS)" testsuite pytest

test-pdns-output:  ## Run pdns-output Java test suite (requires active testbed)
	@if ! docker ps --filter "name=dim-testsuite-mariadb" --format '{{.Names}}' | grep -q 'mariadb'; then \
		echo "[ERROR] The testbed is not running!"; \
		echo "        pdns-output tests require an active PowerDNS test database."; \
		echo "        Please run 'make testbed-start' first before running 'make test-pdns-output'."; \
		exit 1; \
	fi
	@echo "==> Running pdns-output tests..."
	@cd pdns-output && ./gradlew test

test-ndcli:  ## Run ndcli CLI unit tests
	@echo "==> Running ndcli CLI unit tests..."
	@PYTHONPATH="dimclient:ndcli:$${PYTHONPATH:-}" $(PYTHON) -m pytest ndcli/tests/cliparse_test.py

test-e2e:  ## Run E2E CLI scenario tests (t/*.t) in container (optional: TESTS="name.t")
	@if docker ps --filter "name=dim-testsuite" --format '{{.Names}}' | grep -q 'dim-testsuite'; then \
		echo "[ERROR] An active DIM testbed is currently running!"; \
		echo "        E2E scenario tests require a clean, virgin environment to prevent port collisions (port 5000)"; \
		echo "        and database lock contention ('pdns_poller')."; \
		echo "        Please run 'make testbed-stop' first before executing 'make test-e2e'."; \
		exit 1; \
	fi
	@echo "==> Running E2E scenario test suite inside container..."
	@$(COMPOSE) run --rm -e TESTS="$(TESTS)" testsuite ndcli

test-all: test-dim test-pdns-output test-ndcli test-e2e  ## Run all test suites across all components

##@ Documentation

docs:  ## Build HTML documentation with Sphinx (isolated container, no DB)
	@echo "==> Building HTML documentation with Sphinx..."
	@$(COMPOSE) run --rm docs
	@echo ""
	@echo "[OK] HTML documentation built successfully!"
	@echo "  • Local file: file://$(CURDIR)/dim/doc/_build/html/index.html"

docs-serve: docs  ## Build and serve HTML documentation on http://localhost:8080
	@echo "==> Serving documentation on http://localhost:8080 (Ctrl+C to stop)..."
	@python3 -m http.server 8080 --directory dim/doc/_build/html

##@ Live Network Testbed (Docker Compose)

testbed-start:  ## Start all local testbed containers in background (instant start, no slow rebuild)
	@echo "==> Starting local DIM testbed in background..."
	@ids=$$(docker ps -aq --filter "name=dim-testsuite-testsuite-run"); if [ -n "$$ids" ]; then docker rm -f $$ids >/dev/null 2>&1; fi
	$(COMPOSE) up -d --remove-orphans mariadb pdns1 pdns2 pdns-unit dim-mw dim-output
	@echo ""
	@echo "[OK] DIM Testbed is running!"
	@echo "  • Run 'make testbed-shell' for interactive testbed shell (ndcli, dig)"
	@echo "  • Run 'make testbed-logs'  to follow middleware and output logs"
	@echo "  • Run 'make testbed-stop'  to shut down all containers and clean up"

testbed-build:  ## Rebuild testbed container images from scratch
	@echo "==> Rebuilding testbed container images..."
	$(COMPOSE) build mariadb pdns1 pdns2 dim-mw dim-output

testbed-stop:  ## Stop local testbed and tear down containers
	@echo "==> Stopping local DIM testbed..."
	$(COMPOSE) down -v --remove-orphans
	@echo "[OK] Testbed stopped and cleaned up."

testbed-status:  ## Show status of testbed services and ports
	@./dim-testsuite/docker/status.sh

testbed-logs:  ## Follow live logs from DIM middleware and pdns-output daemon
	@$(COMPOSE) logs -f dim-mw dim-output

testbed-seed:  ## Populate testbed with demo zone (test.example.com) and sample records
	@echo "==> Seeding testbed data..."
	@$(COMPOSE) exec dim-mw /src/dim-testsuite/docker/seed.sh

##@ Container Shells

testbed-shell:  ## Open interactive testbed shell (ndcli with tab-completion, dig)
	@$(COMPOSE) exec dim-mw /src/dim-testsuite/docker/testbed-shell.sh

shell-output:  ## Open output daemon shell (pdns-output daemon)
	@$(COMPOSE) exec dim-output /bin/bash

shell-pdns:  ## Open PowerDNS authoritative nameserver shell (pdns1)
	@$(COMPOSE) exec pdns1 /bin/bash

##@ Service Fast-Reload (1s)

restart-mw:  ## Restart dim-mw container to reload Python changes (1s)
	@echo "==> Restarting dim-mw container..."
	@$(COMPOSE) restart dim-mw
	@echo "[OK] dim-mw reloaded!"

restart-output:  ## Fast-reload pdns-output container with incremental Java build (~4s)
	@echo "==> Incrementally compiling pdns-output..."
	@cd pdns-output && ./gradlew shadowJar -x test -q
	@echo "==> Restarting dim-output container..."
	@$(COMPOSE) restart dim-output
	@echo "[OK] dim-output reloaded in ~4s with fresh Java build!"

clean:  ## Clean temporary caches and test artifacts
	rm -rf .pytest_cache dim/.pytest_cache ndcli/.pytest_cache dim-testsuite/.pytest_cache dim/doc/_build
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
