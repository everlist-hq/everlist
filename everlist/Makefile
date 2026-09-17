# agent-hub-v2 — owned process harness (G6)
# RULES: never kill by process name; only PIDs recorded in .run/ are stopped.
# Port conflicts FAIL with instructions instead of killing unknown listeners.
# (Stale processes on ports caused 3 lost debug cycles on 2026-09-08.)

SHELL := /bin/bash
# Overridable for CI (no /opt/venv or local venv there): make test PYTHON=python3 VPY=python3
PYTHON   ?= /opt/venv/bin/python
VPY      ?= venv/bin/python
HUB_PORT ?= 8802
WEBCHAT_PORT ?= 8804
RUNDIR   ?= .run
# C12 review lesson: the test gate must NEVER share state, PIDs, or seeds with
# a live stack. make test runs a fully isolated stack (own rundir, port, seed,
# no standalone wrapper) so it cannot kill or collide with `make up` again.
GATE_RUNDIR   ?= .run/gate
GATE_PORT     ?= 8803
GATE_SEEDFILE ?= .secrets/gate_seed

.PHONY: up down status test clean selfcheck demo-seed demo-reset gate-up gate-down

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
		HUB_STATE_FILE=$$PWD/$(RUNDIR)/state.json nohup $(VPY) app.py $(HUB_PORT) > $(RUNDIR)/hub.log 2>&1 & echo $$! > $(RUNDIR)/hub.pid
	@ok=""; for i in $$(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$(HUB_PORT)) 2>/dev/null && { exec 3>&- 3<&-; ok=1; break; }; sleep 0.2; done; \
	[ -n "$$ok" ] || { echo "[up] hub FAILED to start; last log lines:"; tail -n 20 $(RUNDIR)/hub.log; exit 1; }
	@echo "[up] wrapper -> $(RUNDIR)/wrapper.log"
	@HUB_AGENT_SEED=$$(cat "$(SEEDFILE)") HUB_URL=http://localhost:$(HUB_PORT) nohup $(VPY) wrapper.py > $(RUNDIR)/wrapper.log 2>&1 & echo $$! > $(RUNDIR)/wrapper.pid
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

# C12 review lesson: the gate stack is FULLY isolated from the live stack —
# own rundir (state + lock + pids), own port, no wrapper (test_client embeds
# one with a throwaway seed). make test can never kill or collide with `make up`.

gate-up: ## start the ISOLATED gate hub (used by make test only)
	@mkdir -p $(GATE_RUNDIR)
	@# C12 enforced (2026-09-16): gate state used to persist forever, so the
	@# demo listing hit 30/30 capacity and the A2A E2E failed after dozens of
	@# runs. Every gate run now starts fresh; the hub auto-seeds its built-in
	@# demo listings on empty state.
	@if [ -s $(GATE_RUNDIR)/state.json ]; then \
		mkdir -p $(GATE_RUNDIR)/stale; \
		mv $(GATE_RUNDIR)/state.json $(GATE_RUNDIR)/stale/state.json.$$(date +%s); \
		echo "[gate-up] archived stale gate state -> stale/"; fi
	@if (exec 3<>/dev/tcp/127.0.0.1/$(GATE_PORT)) 2>/dev/null; then \
		echo "FATAL: gate port $(GATE_PORT) already in use."; exit 1; fi
	@HUB_STATE_FILE=$$PWD/$(GATE_RUNDIR)/state.json nohup $(PYTHON) app.py $(GATE_PORT) > $(GATE_RUNDIR)/hub.log 2>&1 & echo $$! > $(GATE_RUNDIR)/hub.pid
	@ok=""; for i in $$(seq 1 50); do (exec 3<>/dev/tcp/127.0.0.1/$(GATE_PORT)) 2>/dev/null && { exec 3>&- 3<&-; ok=1; break; }; sleep 0.2; done; \
	[ -n "$$ok" ] || { echo "[gate-up] hub FAILED to start; last log lines:"; tail -n 20 $(GATE_RUNDIR)/hub.log; exit 1; }
	@echo "[gate-up] isolated hub on :$(GATE_PORT) pid=$$(cat $(GATE_RUNDIR)/hub.pid)"

gate-down: ## stop ONLY the gate hub (never touches live pids)
	@if [ -f $(GATE_RUNDIR)/hub.pid ]; then pid=$$(cat $(GATE_RUNDIR)/hub.pid); \
		if kill -0 $$pid 2>/dev/null; then \
			kill -TERM $$pid 2>/dev/null || true; \
			for i in $$(seq 1 20); do kill -0 $$pid 2>/dev/null || break; sleep 0.2; done; \
			if kill -0 $$pid 2>/dev/null; then kill -KILL $$pid 2>/dev/null || true; fi; \
		fi; rm -f $(GATE_RUNDIR)/hub.pid; fi
	@echo "[gate-down] complete"

status: ## show recorded process states
	@for f in $(RUNDIR)/hub.pid $(RUNDIR)/wrapper.pid $(RUNDIR)/webchat.pid; do \
		if [ -f "$$f" ]; then pid=$$(cat "$$f"); \
			kill -0 "$$pid" 2>/dev/null && echo "$$f: $$pid RUNNING" || echo "$$f: $$pid DEAD"; \
		else echo "$$f: none"; fi; done

webchat-up: ## start webchat UI (chat-first web surface) on :8804
	@if [ -f $(RUNDIR)/webchat.pid ] && kill -0 $$(cat $(RUNDIR)/webchat.pid) 2>/dev/null; then \
		echo "[webchat] already running pid=$$(cat $(RUNDIR)/webchat.pid)"; exit 0; fi
	@echo "[webchat] starting -> $(RUNDIR)/webchat.log"
	@WEBCHAT_HUB_URL=http://localhost:$(HUB_PORT) nohup $(VPY) webchat.py $(WEBCHAT_PORT) > $(RUNDIR)/webchat.log 2>&1 & echo $$! > $(RUNDIR)/webchat.pid
	@sleep 1
	@if (exec 3<>/dev/tcp/127.0.0.1/$(WEBCHAT_PORT)) 2>/dev/null; then exec 3>&- 3<&-; echo "[webchat] up on :$(WEBCHAT_PORT) (pid $$(cat $(RUNDIR)/webchat.pid))"; else echo "[webchat] FAILED; log:"; tail -n 10 $(RUNDIR)/webchat.log; exit 1; fi

webchat-down: ## stop ONLY the webchat process (never touches hub/wrapper pids)
	@if [ -f $(RUNDIR)/webchat.pid ]; then pid=$$(cat $(RUNDIR)/webchat.pid); \
		kill "$$pid" 2>/dev/null && echo "[webchat] stopped pid=$$pid" || echo "[webchat] not running"; \
		rm -f $(RUNDIR)/webchat.pid; else echo "[webchat] not running"; fi

test: ## G2 suite + A2A E2E against an ISOLATED gate stack (never touches live)
	@mkdir -p $(GATE_RUNDIR)
	@$(PYTHON) -c 'import cryptography, httpx' 2>/dev/null || { echo 'deps missing for $(PYTHON): pip install -r requirements.txt'; exit 1; }; \
	$(VPY) -c 'import cryptography, uagents' 2>/dev/null || { echo 'deps missing for $(VPY): python -m venv venv && venv/bin/pip install -r requirements.txt'; exit 1; }; \
	@$(MAKE) --no-print-directory gate-down >/dev/null 2>&1 || true
	@trap '$(MAKE) --no-print-directory gate-down >/dev/null 2>&1' EXIT; \
		echo "=== G2 suite ==="; \
		$(PYTHON) test_suite.py || exit 1; \
		echo "=== Storage adapter (1.1 file+sqlite) ==="; \
		$(PYTHON) test_storage.py || exit 1; \
		echo "=== Storage E2E (restart/idempotency/fail-closed, file+sqlite) ==="; \
		$(PYTHON) test_storage_e2e.py || exit 1; \
		echo "=== Hardening suite (email/archive/ids) ==="; \
		$(PYTHON) test_hardening.py || exit 1; \
		echo "=== Concurrency battery (B2) ==="; \
		$(PYTHON) test_concurrency.py || exit 1; \
		echo "=== SDK accounts (B5) ==="; \
		$(PYTHON) test_sdk_accounts.py || exit 1; \
		echo "=== S6 review integrity (anti-fake-review) ==="; \
		$(VPY) -u test_s6_review_integrity.py || exit 1; \
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
		echo '=== B-phase booking notifications ==='; $(PYTHON) test_notify.py || exit 1; \
		echo '=== H7 account deletion ==='; $(PYTHON) test_account_deletion.py || exit 1; \
		echo '=== H9 single-listing fetch ==='; $(PYTHON) test_h9_listing_fetch.py || exit 1; \
		echo '=== H10 booking status ==='; $(PYTHON) test_h10_booking_status.py || exit 1; \
		echo '=== H11 SDK robustness ==='; $(PYTHON) test_h11_sdk_robustness.py || exit 1; \
		echo '=== H12 request logging ==='; $(PYTHON) test_h12_request_logging.py || exit 1; \
		echo '=== H13 openapi contract ==='; $(PYTHON) test_h13_openapi.py || exit 1; \
		echo '=== H14 selfcheck tool ==='; $(PYTHON) test_h14_selfcheck.py || exit 1; \
		echo '=== H15 services vertical ==='; $(PYTHON) test_h15_services.py || exit 1; \
		echo '=== H16 demo seed tools ==='; $(PYTHON) test_h16_demo_seed.py || exit 1; \
		echo '=== H17 red-team pass 2 ==='; $(PYTHON) test_h17_redteam.py || exit 1; \
		echo '=== M5 escrow_ref ==='; $(PYTHON) test_m5_escrow_ref.py || exit 1; \
		echo '=== M6 indexer client ==='; $(PYTHON) test_m6_indexer.py || exit 1; \
		echo '=== M7 mirror sync ==='; $(PYTHON) test_m7_sync.py || exit 1; \
		echo '=== M8 E2E chain booking ==='; $(PYTHON) test_m8_e2e_chain.py || exit 1; \
		echo '=== M9 payout key ==='; $(PYTHON) test_m9_payout.py || exit 1; \
		echo '=== M10 secret hygiene ==='; $(PYTHON) test_m10_secrets.py || exit 1; \
		echo '=== M14 Tier-2 sign-in ==='; $(PYTHON) test_m14_tier2.py || exit 1; \
		echo '=== S1 red-team pass 3 ==='; $(PYTHON) test_s1_redteam3.py || exit 1; \
		echo '=== C2 search upgrades ==='; $(PYTHON) test_c2_search.py || exit 1; \
		echo '=== C3 my-bookings ==='; $(PYTHON) test_c3_bookings.py || exit 1; \
		echo '=== C4 reputation ==='; $(PYTHON) test_c4_rating.py || exit 1; \
		echo '=== C5 community schemas ==='; $(PYTHON) test_c5_schemas.py || exit 1; \
		echo '=== C12 payment terms ==='; $(PYTHON) test_c12_payment_terms.py || exit 1; \
		echo '=== C9 webchat UI ==='; $(VPY) -u test_webchat.py || exit 1; \
		echo '=== C9 NLU + card format ==='; $(PYTHON) test_nlu.py || exit 1; \
		echo "=== A2A E2E (isolated gate hub :$(GATE_PORT)) ==="; \
		$(MAKE) --no-print-directory gate-up >/dev/null || exit 1; \
		HUB_PORT=$(GATE_PORT) $(VPY) -u test_client.py || exit 1; \
		echo "=== make test: ALL PASSED ==="

clean: ## remove .run/ (harness-owned ONLY; never touches other files)
	@$(MAKE) --no-print-directory down >/dev/null 2>&1 || true
	@rm -rf $(RUNDIR)
	@echo "[clean] removed $(RUNDIR)/ (harness-owned only)"

audit: ## H6: pip-audit the dependency tree (waivers documented in SPEC 17)
	@$(VPY) -m pip_audit --version >/dev/null 2>&1 || { echo "[audit] pip-audit missing — install with: $(VPY) -m pip install pip-audit"; exit 2; }
	@$(VPY) -m pip_audit -r requirements.txt --ignore-vuln PYSEC-2026-3002 --ignore-vuln PYSEC-2026-1325 \
		&& echo "[audit] no known vulnerabilities (2 waived — reasoning in SPEC 17)"

selfcheck: ## on-demand read-only health probe against the running hub (H14)
	@HUB_URL=http://127.0.0.1:$(HUB_PORT) $(PYTHON) tools/selfcheck.py

demo-seed: ## seed the running hub with demo_listings.json (H16; refuses if listings exist)
	@$(PYTHON) tools/seed_demo.py --hub http://127.0.0.1:$(HUB_PORT)

demo-reset: ## one-command reproducible pilot demo: archive state -> fresh stack -> seed (H16)
	@$(MAKE) --no-print-directory down >/dev/null 2>&1 || true
	@if [ -s $(RUNDIR)/state.json ]; then \
		mv $(RUNDIR)/state.json $(RUNDIR)/state.pre-demo-$$(date +%Y%m%d-%H%M%S).json; \
		echo "[demo] previous state archived in $(RUNDIR)/"; fi
	@printf '%s' '{"listings": [], "bookings": [], "ledger": []}' > $(RUNDIR)/state.json; \
		echo "[demo] empty snapshot written (H16 test-proven: replaces built-in seed data)"
	@$(MAKE) --no-print-directory up >/dev/null
	@$(MAKE) --no-print-directory demo-seed
