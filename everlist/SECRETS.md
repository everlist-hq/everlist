# Secrets Inventory (S3)

Every secret EverList touches, where it lives, and where it must NEVER appear.
Rule: the hub stores **public keys and hashes only** — secrets live in wallets, env, or `.secrets/` (0600, gitignored).

## Operator secrets

| Secret | Stored | Rotation |
| --- | --- | --- |
| Booking signing key (`HUB_BOOKING_KEY`) | env or `.secrets/` | restart with new env; token `gen` counters kill old tokens on recovery/rotation |
| Admin key (`HUB_ADMIN_KEY`) | env or `.secrets/` | restart; dev default is FATAL under `HUB_ENV=production`, loud WARNING otherwise |
| Email app password (optional SMTP) | `.secrets/email.env` (0600) | Google account |
| Agentverse key | `.secrets/agentverse_key` (0600) | Agentverse UI |
| Wrapper agent seed | `.secrets/agent_seed` (0600) | regenerate = new agent identity |

## User secrets (never stored server-side)

| Secret | Holder | Hub stores |
| --- | --- | --- |
| Account code (`acct-…`, shown once) | user | SHA-256 hash only |
| Ed25519 login seed | user wallet/file | public key only |
| Midnight payout secret key | organizer wallet | **nothing** — 64-hex coin PUBLIC key only (`payout_pk`) |
| Midnight holder secret | user | commitment hash only |
| Manage code (`mgr-…`) | listing owner | hash only |
| Booking secret | buyer | value itself (needed to fetch; ids unguessable) |

## Never-appear walls (enforced by tests)

- `test_m10_secrets.py` — no seed/token material in state.json, logs, backups, or git (structural scan runs on fresh clones)
- `test_s1_redteam3.py` — payout endpoint accepts 64-hex PUBLIC keys only
- Public ledger — no identity data in ratings (C4) or mirror entries (M7)

## Leak response → see INCIDENT.md
