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
