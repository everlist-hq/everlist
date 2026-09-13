## 2026-09-11 S-track Sprint

| Item | Status | Artifacts |
| --- | --- | --- |
| S1 Red-team pass 3 | ✅ 16/16 suite, Makefile wired | `test_s1_redteam3.py` |
| S2 Rate-limit sweep | ✅ pre-auth limits + env knobs | app.py + S1 tests |
| S3 Secrets inventory | ✅ live doc | `SECRETS.md` |
| S4 Incident playbook | ✅ live doc | `INCIDENT.md` |
| S5 Dependency hygiene | ✅ SPEC §17 updated | pip check clean, pip-audit waived |

## 2026-09-11 P1 Pilot Prep

| Item | Status | Artifacts |
| --- | --- | --- |
| Outreach kit | ✅ EN/DE templates | `docs/pilot-outreach-kit.md` |
| Lead tracker | ✅ progress table | `docs/pilot-lead-list.md` |

## Records

- `docs/backlog-v3.md` → P1 prep ticked
- `docs/pilot-plan.md` → wedge definition aligned (2 organizers, 3 bookings)
- `docs/worklog.md` → updated
## 2026-09-12 — Full tech review (pre-VPS-launch)
- Live stack verified: hub+wrapper up, /search /suggest /descriptor 200, demo data intact
- make test re-run green (EXIT=0, 59 PASSED lines) after resolving gate-vs-live-stack port collision (expected behavior)
- FOUND+FIXED CI regression: 6 test suites spawned children via hardcoded /opt/venv/bin/python (introduced during deploy-script work) — GitHub runners lack that path, CI red since 7f4add2. Restored portable sys.executable (pattern already used by chat_e2e/single_instance/h14); all 6 suites re-passed under venv runtime (CI-equivalent); both trees synced
- Registry 16/16, x402 11/11+12/12 under venv runtime confirmed

## 2026-09-13 — P2: private escrow deals (owner-approved, plan C)
- New: `visibility: private` (listed, not public — owner-corrected naming, not 'unlisted') + one-time claim codes (pvt-…, sha256-stored, server-only field, reserved from community schemas).
- No-oracle: GET/booking of private deals without valid claim == unknown id (404, same text shape); claim via X-Claim-Code, ?claim=, or booking field.
- Discovery walls: /search (FTS + legacy), /listings public branch, /suggest, events quick-routes all exclude private; owner token sees own deals in /listings and can GET without claim.
- Manage: make_private (mints FRESH claim, old dies) / make_public (re-lists publicly); added to action allowlist + OpenAPI.
- New community vertical `p2p` (secondhand/freelance/tickets/custom; booking identity buyer).
- Chat: `deal …` one-liner (quick + rich, C12 rail/refund_window/deposit keys), inline claim in `book <id> <pvt-…> <name>`, claim-aware SDK guidance, help rows; live B10 help-honesty suite still 12/12.
- SPEC §20 added. Tests: test_p2_private_deals.py 36/36; full isolated gate ALL PASSED (incl. A2A E2E).

## 2026-09-13 — C11 verified-buyer gating shipped

- Listing field `require_verified_buyer` (strict boolean, creation + manage edit, owner-only flips) wiring the M14
  Tier-2 machinery as the approved merchant lever (SPEC section 22).
- `/book` gate uses the server-side `acct_verified` predicate only; the client-asserted human_verified stub can never
  satisfy it. 403 carries the requirement + the verify-midnight fix; bookings store server-derived `verified_by`.
- Chat: `verified_only: yes|no` rich key (strict parsing), gate shown in the listing face, help updated. OpenAPI notes
  for /listings + /book.
- test_c11_verified_buyer.py: 34/34 (boolean walls, stub walls incl. unverified accounts, admin-vouch + midnight-zk
  booking paths, revoked fail-closed, ungated regression, private-deal composition = two independent walls, chat E2E).
- Full gate green (ALL PASSED incl. A2A E2E); live stack restarted + live-smoked (stub walled on :8802 as designed).

## 2026-09-13 — C9: chat-first web UI (owner proposed the website; built same day)
- Owner message: "How about a website UI? … a normal chat box for AI. We interact only like that with the website. Like: 'what are you looking for?'" — this IS backlog C9, so the owner GO landed with the idea. Built the deterministic core now; LLM layer on top stays an explicit owner call (model/provider/cost).
- New `webchat.py` (stdlib-only, house pattern): third surface for the SAME chat brain — every message goes through `chatlib.handle_text()` unchanged (Agentverse wrapper, CLI, now browser; no new protocol surface, SPEC §1 hub-stays-agent-only honored). Serves `static/` + `POST /api/chat` + `/api/health` + `/api/reset`; binds 127.0.0.1 (Caddy proxies chat.<domain> on the VPS; port never directly exposed); :8804 (8803 is the test gate).
- Security: session = server-minted 192-bit HttpOnly/SameSite=Lax cookie (Secure on public hosts) that DOUBLES as the chatlib sender key — client cannot forge a sender, no extra session store; per-sender (8 burst/4 per-min) + per-IP (30/20) token buckets; 32KB body cap; 413/415/400/404 walls; strict CSP (`script-src 'self'`, no inline), nosniff, no-referrer; path-traversal wall; request-line-only logging (message content never logged); HEAD/DELETE → 501.
- UI (`static/`): dark chat-first single page — "What are you looking for?" greeting, free-text box + quick chips, XSS-safe textContent-only rendering, health dot, latency footer, new-session button; no transcript persistence (seeds are shown-once material — browser memory only, deliberate).
- Tests: new `test_webchat.py` — 20/20 PASS (static+headers, traversal wall, signup→one-time-seed→whoami session continuity, fallback search 'jazz', filtered 'pizza under 10', reset→anonymous, 413/415/400, 429 after burst) wired into `make test` (before the A2A gate stage). Operator targets: `make webchat-up` / `webchat-down`, status tracks webchat.pid. Full gate: make test ALL PASSED with the new suite included.
- Deploy: `tools/deploy.sh` gains chat.<domain> Caddy site + `everlist-webchat.service` (After/Requires everlist) + enable/restart + verify echo; `docs/DEPLOY.md` documents the chat subdomain DNS (chat A record → same IP), health check, and journalctl lines.
- Honest notes: seed listings display 'SOLD OUT' in this fresh-hub test because registered==capacity in seed data (pre-existing chatlib formatting, correct behavior); chatlib brain calls serialized behind one lock (single-threaded in wrapper, threaded here — pilot-grade, revisit under real concurrency).
