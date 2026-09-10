# agent-hub-v2 — owned process harness (G6)
# RULES: never kill by process name; only PIDs recorded in .run/ are stopped.
# Port conflicts FAIL with instructions instead of killing unknown listeners.
# (Stale processes on ports caused 3 lost debug cycles on 2026-09-08.)

SHELL := /bin/bash
# Overridable for CI (no /opt/venv or local venv there): make test PYTHON=python3 VPY=python3
PYTHON   ?= /opt/venv/bin/python
VPY      ?= venv/bin/python
HUB_PORT ?= 8802
RUNDIR   := .run

.PHONY: up down status test clean

# canonical wrapper identity seed (gitignored, TESTNET ONLY)
SEEDFILE := .secrets/agent_seed

up: ## start hub+wrapper, record PIDs in .run/
	@mkdir -p $(RUNDIR) $(dir $(SEEDFILE))
	@if [ ! -s "$(SEEDFILE)" ]; then umask 077; openssl rand -hex 32 > "$(SEEDFILE)"; \
		echo "[seed] generated new canonical seed -> $(SEEDFILE) (gitignored, testnet only)"; \
	else echo "[seed] reusing canonical seed $(SEEDFILE)"; fi
	@if (exec 3<>/dev/tcp/127.0.0.1/$(HUB_PORT)) 2>/dev/null; then \
		exec 3>&- 3<&-; \
		echo "FATAL: port $(HUB_PORT) is already in use by an unknown listener."; \
		echo "  identify it:  ss -ltnp | grep $(HUB_PORT)"; \
		echo "  then stop it yourself, OR run:  make up HUB_PORT=8803"; \
		exit 1; fi
	@echo "[up] hub on :$(HUB_PORT) -> $(RUNDIR)/hub.log"
	@if [ -f .secrets/email.env ]; then . ./.secrets/email.env; fi; \
		HUB_STATE_FILE=$$PWD/$(RUNDIR)/state.json nohup $(PYTHON) app.py $(HUB_PORT) > $(RUNDIR)/hub.log 2>&1 & echo $$! > $(RUNDIR)/hub.pid
	@ok=""; for i in $$(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$(HUB_PORT)) 2>/dev/null && { exec 3>&- 3<&-; ok=1; break; }; sleep 0.2; done; \
	[ -n "$$ok" ] || { echo "[up] hub FAILED to start; last log lines:"; tail -n 20 $(RUNDIR)/hub.log; exit 1; }
	@echo "[up] wrapper -> $(RUNDIR)/wrapper.log"
	@HUB_AGENT_SEED=$$(cat "$(SEEDFILE)") nohup $(VPY) wrapper.py > $(RUNDIR)/wrapper.log 2>&1 & echo $$! > $(RUNDIR)/wrapper.pid
	@sleep 2
	@echo "[up] done. hub pid=$$(cat $(RUNDIR)/hub.pid), wrapper pid=$$(cat $(RUNDIR)/wrapper.pid). stop with: make down"

down: ## graceful TERM then KILL after timeout — ONLY recorded PIDs
	@for f in $(RUNDIR)/wrapper.pid $(RUNDIR)/hub.pid; do \
		if [ -f "$$f" ]; then pid=$$(cat "$$f"); \
			if kill -0 "$$pid" 2>/dev/null; then \
				echo "[down] TERM $$pid ($$f)"; kill -TERM "$$pid" 2>/dev/null || true; \
				for i in $$(seq 1 20); do kill -0 "$$pid" 2>/dev/null || break; sleep 0.2; done; \
				if kill -0 "$$pid" 2>/dev/null; then echo "[down] KILL $$pid"; kill -KILL "$$pid" 2>/dev/null || true; fi; \
			else echo "[down] $$f: pid $$pid not running (stale)"; fi; \
			rm -f "$$f"; \
		else echo "[down] $$f: none"; fi; done
	@echo "[down] complete (only recorded PIDs were touched)"

status: ## show recorded process states
	@for f in $(RUNDIR)/hub.pid $(RUNDIR)/wrapper.pid; do \
		if [ -f "$$f" ]; then pid=$$(cat "$$f"); \
			kill -0 "$$pid" 2>/dev/null && echo "$$f: $$pid RUNNING" || echo "$$f: $$pid DEAD"; \
		else echo "$$f: none"; fi; done

test: ## G2 suite + A2A E2E against an owned hub; trap guarantees cleanup; bounded
	@mkdir -p $(RUNDIR)
	@$(MAKE) --no-print-directory down >/dev/null 2>&1 || true
	@trap '$(MAKE) --no-print-directory down >/dev/null 2>&1' EXIT; \
		echo "=== G2 suite ==="; \
		$(PYTHON) test_suite.py || exit 1; \
		echo "=== Hardening suite (email/archive/ids) ==="; \
		$(PYTHON) test_hardening.py || exit 1; \
		echo "=== Concurrency battery (B2) ==="; \
		$(PYTHON) test_concurrency.py || exit 1; \
		echo "=== SDK accounts (B5) ==="; \
		$(PYTHON) test_sdk_accounts.py || exit 1; \
		echo "=== Chat E2E via real wrapper (B6) ==="; \
		$(VPY) -u test_chat_e2e.py || exit 1; \
		echo "=== Public read limits (B7) ==="; \
		$(PYTHON) test_read_limits.py || exit 1; \
		echo "=== Backup rotation (B8) ==="; \
		$(PYTHON) test_backups.py || exit 1; \
		echo "=== Restore drill (H2) ==="; \
		$(PYTHON) test_restore_drill.py || exit 1; \
		echo "=== Single-instance guard (H4) ==="; \
		$(PYTHON) test_single_instance.py || exit 1; \
		echo "=== Write-path limiter floods (H5) ==="; \
		$(PYTHON) test_write_limits.py || exit 1; \
		echo "=== Chat help honesty (B10) ==="; \
		$(PYTHON) test_chat_help.py || exit 1; \
		echo "=== A2A E2E (hub :$(HUB_PORT) via make up) ==="; \
		$(MAKE) --no-print-directory up HUB_PORT=$(HUB_PORT) >/dev/null || exit 1; \
		$(VPY) -u test_client.py || exit 1; \
		echo "=== make test: ALL PASSED ==="

clean: ## remove .run/ (harness-owned ONLY; never touches other files)
	@$(MAKE) --no-print-directory down >/dev/null 2>&1 || true
	@rm -rf $(RUNDIR)
	@echo "[clean] removed $(RUNDIR)/ (harness-owned only)"

audit: ## H6: pip-audit the dependency tree (waivers documented in SPEC 17)
	@$(VPY) -m pip_audit --version >/dev/null 2>&1 || { echo "[audit] pip-audit missing — install with: $(VPY) -m pip install pip-audit"; exit 2; }
	@$(VPY) -m pip_audit -r requirements.txt --ignore-vuln PYSEC-2026-3002 --ignore-vuln PYSEC-2026-1325 \
		&& echo "[audit] no known vulnerabilities (2 waived — reasoning in SPEC 17)"
