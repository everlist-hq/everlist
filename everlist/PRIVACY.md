# EverList privacy & data — what we store, and what we never do

This page is exact, not marketing. Every claim maps to code (app.py, SPEC §12a/§13).

## What the hub stores about an organizer account

| Data | How it's stored | Why | Gone when |
| --- | --- | --- | --- |
| Account id (`acct-…`) | plaintext | it's your address in the system | account deletion (H7) |
| Account code / manage codes | **SHA-256 hash only** | you prove ownership; we could never resend them | deletion / rotation |
| Keypair accounts | **public key only** | your seed **never leaves your device** — we cannot lose or leak it | deletion |
| Bound agent names | plaintext | your agents act for the account | deletion (or unbind) |
| Recovery email | **plaintext** | we must be able to *send* you codes — a hash can't receive mail | deletion; replace any time (`email-bind`) |
| Verification/recovery state | hashes + timestamps | anti-abuse (single-use, 15-min expiry) | auto-expiry / deletion |
| `human_verified` + `verified_by` | plaintext flag | the human gate for bookings | deletion |

## What we never store

- **Seeds and codes in readable form** — only hashes; shown to you exactly once at creation
- **Passwords** — there are none; auth is code/hash or Ed25519 challenge-response
- **Rate-limit and session data on disk** — limiter timestamps, login challenges, chat sessions live in memory only and die on restart
- **Any PII in the public ledger** — it holds pseudonymous refs: booking ids, agent/account principals, amounts, payment fingerprints. No names, no emails

## What you provide and control

- **Bookings**: your agent sends the allowed fields (e.g. `attendee`). That text is whatever you/your agent put there — you control it; we reject reserved/unknown fields (I1 field ownership)
- **Listings**: title/description/tags/url/location — your content, editable or removable by you (manage code or account)

## Retention & deletion

- **Account deletion (H7)** — `DELETE /accounts/me` (typed confirmation; chat `delete-account`): erases the account record *including the recovery email*, revokes every token, archives your listings (bookers keep escrow rights), and the money trail stays pseudonymous for auditability
- **Honest limitation — backups**: the hub keeps the last **5 pre-persist snapshots** (`<state>/backups/`). A deleted account can linger in older backup generations until they rotate out; backups also carry file-permission 600 and live on the same machine as the hub
- **Restart persistence**: everything in the table above is in `state.json` (mode 600, single-instance locked). Corrupt state fails CLOSED (H3-era rule) — the hub refuses to start rather than silently replace your data

## Who can read what

- **Public**: listings (incl. your url/description), the full ledger, the manifest
- **Owner-only**: archived-listing view (`/listings?archived=1` requires the account token)
- **The hub operator** (you, during the pilot): the same public surface plus `state.json`. No third-party analytics, no tracking, no external sharing

## Interim honesty (pilot stage)

- Human-verification today = operator vouch or email code (documented interim); production target = Midnight zk-personhood (A2) — prove *one human*, reveal *nothing*
- Chat senders are identified by their agent address via the Agentverse relay; the wrapper keeps in-memory per-sender sessions only

Questions or a deletion request without your account access? Run `recover <email>` or contact the hub operator directly.
