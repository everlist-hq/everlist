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
## 2026-09-12 — Deployment guide finalized for actual purchase
- docs/DEPLOY.md rewritten: provider = cloudserver.net LEB special (2GB/1vCPU/50GB, LA, Ubuntu 24.04, $23.88/yr ordered 2026-09-12 via PayPal, pending provisioning)
- corrected 2026 price table (Hetzner post-hike €7.79, Godlike fallback €3.49/mo), added HUB_AGENT_SEED + wrapper venv to script description, migration cheat-sheet row
- Synced to experiments/agent-hub-v2/docs/DEPLOY.md (TREE_SYNCED)

## 2026-09-13 — C12 Payment Terms + Gate Isolation

| Item | Status | Artifacts |
| --- | --- | --- |
| C12 payment terms (SPEC §19a–d) | ✅ suite 25/25 | `test_c12_payment_terms.py`, app.py, chatlib.py |
| Isolated test gate (make test) | ✅ full gate 672 PASS / 0 FAIL incl. A2A E2E | Makefile (`gate-up`/`gate-down`, GATE_RUNDIR/GATE_PORT) |

- Hub: `payment_terms {rail: escrow|instant, refund_window_hours, deposit_required}` — per-vertical defaults injected at create and at load (legacy migration); validation walls (bad rail, window bounds, instant+window, deposit > price, unknown keys).
- Consent: paid bookings on custom-terms listings must echo `accepted_payment_terms` exactly (409 carries the terms); default-terms bookings stay friction-free; free listings exempt; every booking keeps a server-copied terms snapshot (later edits never rewrite done deals); owner-editable pre-booking via manage (null = reset).
- Instant rail: books as escrow `DIRECT` (settled at booking) — no confirm/cancel (honest errors), rating opens, `escrow_ref` rejected.
- Chat: rich `rail:` / `refund_window:` / `deposit:` keys, terms shown in listing display and paid-booking guidance; OpenAPI notes updated; SDK unchanged (echo passes through `book(**fields)`).
- Gate isolation lesson: `make test` previously shared `.run/` with the live stack — its `down` step TERM'd the live wrapper and `up` hit the H4 state lock. The gate now runs a fully isolated stack (own rundir/port, no standalone wrapper) and can never collide with `make up` again.

