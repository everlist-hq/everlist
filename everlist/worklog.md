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
