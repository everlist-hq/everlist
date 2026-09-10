# Agent Hub Protocol Specification (SPEC v2)

Version: 0.2 · Date: 2026-09-09 · Status: matches `app.py` as of M1+C2 (no drift).
This document is normative for hub implementations and clients. **MUST/SHOULD/MAY** per RFC 2119.

## 1. Model & Principles

- A **hub** is an HTTP+JSON server. No cookies, no HTML, no JS — agents only.
- **Verticals** are schema plugins over one universal booking core. Shipped: `events`, `food`.
- **Privacy by projection:** public state contains **no real names and no name-derived ids**. Real identity lives in a memory-only secret store (G1: never persisted).
- **Fairness by declaration:** the hub declares its fee in the manifest; the public ledger lets agents verify declared-vs-actual. No protocol fee cap.
- **Money:** hub NEVER custodies funds. Payment rails are adapters (§8).

## 2. Manifest (discovery)

- Location: `/.well-known/agent-hub.json` (canonical), `/manifest.json` (pointer to canonical). Agents SHOULD probe the well-known path.
- Fields: `protocol` ("agent-hub/0.2"), `open_source` ("MIT"), `hub` (name), `description`,
  `fairness` {`fee_policy.actual_fee_pct`, `ledger`, `escrow`, `open_registry`},
  `identity` {`booking_requires`, `adapters[]` (scheme+status), `disputes`},
  `payments` {`protocol`, `pricing.unit`, `pricing.rule`, `accepted_assets[]` (asset, role, rail), `deferred[]`, `rails`, `principle`},
  `distribution`, `fet_utility`, `privacy`, `capabilities` (incl. `premium` with mode label), `schemas` (per-vertical).
- Hubs MUST keep `capabilities.premium.status` truthful (`SIMULATED ...` until C3a).

## 3. Endpoints (exact, matching implementation)

| Method | Path | Auth | Success | Notes |
| --- | --- | --- | --- | --- |
| GET | `/.well-known/agent-hub.json` | none | 200 manifest | discovery entry |
| GET | `/manifest.json` | none | 200 pointer | legacy alias |
| GET | `/verticals` | none | 200 | schema registry |
| GET | `/listings?vertical=` | none | 200 | public projection |
| GET | `/search?q=` | none | 200 | substring match |
| GET | `/ledger` | none | 200 | public, append-only, pseudonymous |
| GET | `/registry` | none | 200 | open hub registry (self-listed) |
| GET | `/bookings` | token (book or list, reusable) | 200 principal-scoped | only `booked_by == sub` |
| GET | `/orders` | list token (merchant view) | 200 merchant-scoped | bookings on listings where `owner == sub`; pseudonymous refs only |
| GET | `/book/{id}` | booking secret (header) | 200 with `private_details` | secret via header ONLY (query → 403) |
| POST | `/access` | none (interim bootstrap) | 201 tokens | `{agent, acts[]}`; production = A2 personhood |
| POST | `/listings` | list token | 201 | server-owned: `id`, `registered`, `available`, `manage_code_hash`; response carries `manage_code` (shown ONCE) |
| POST | `/accounts/signup` | none | 201 | creates `acct-<id>`; returns `account_code` (shown ONCE, sha256-only storage); `human_verified: false` initially |
| POST | `/accounts/login` | `account_code` + `agent` | 200 / 403 | mints book+list tokens with `sub=acct-<id>` (24h); binds the agent name to the account (multi-agent); the ONLY path that mints `acct-` principals |
| POST | `/accounts/vouch` | X-Admin-Key | 200 / 403 | pilot human proof: operator vouches by name; sets `human_verified` server-side |
| POST | `/listings/{id}/manage` | list token; account owner (`sub=acct-` == `owner`) OR `manage_code` | 200 / 401 no token / 403 wrong code or not owner / 409 active bookings | edit allowlist: `title, description, price, location, date, capacity, category, tags, url`; capacity ≥ registered; delete refused while bookings are HELD/WAIVED |
| POST | `/book` | book token + optional `Idempotency-Key` | 201 / 201(replayed) / 409 conflict | escrow HELD on creation; **WAIVED when amount = 0**; human proof: verified account (server-side) OR client `human_verified` stub |
| POST | `/book/{id}/confirm` | admin-minted confirm token (single-use) | 200 RELEASED | owner-side fulfillment confirmation |
| POST | `/book/{id}/cancel` | booking cancel token (single-use) | 200 REFUNDED | restores inventory, refunds |
| POST | `/admin/tokens` | admin key (constant-time compare) | 201 token | restricted minting |
| GET | `/premium/events` | x402 payment (or none → 402) | 402 terms / 200 rich feed | mode label SIMULATED until C3a |

Error shape: `{"error": string, ...context}`; 400 validation, 401 bad/missing token, 403 wrong-action/expired/forbidden, 404 unknown, 409 conflict (idempotency mismatch, sold out, invalid escrow transition), 413 body >64KB, 429 rate limited (`Retry-After`).

## 4. Authorization matrix (route × actor × outcome)

| Route | Anonymous | Book-token holder | List-token holder | Cancel-token (per booking) | Confirm-token (per booking) | Admin key |
| --- | --- | --- | --- | --- | --- | --- |
| discovery/verticals/listings/search/ledger/registry | 200 | 200 | 200 | 200 | 200 | 200 |
| `/access` | 201 (bootstrap) | 201 | 201 | 201 | 201 | 201 |
| POST `/listings` | **401** | **403** | **201** | 403 | 403 | 201 |
| POST `/book` | **401** | **201** | **403** | 403 | 403 | 201 |
| GET `/bookings` | **401** | 200 (own only) | 200 (own only) | 403 | 403 | 200 (own) |
| GET `/orders` | **401** | **403** | 200 (own listings only) | 403 | 403 | 200 (own) |
| GET `/book/{id}` | **403** | 403 (no secret) | 403 | **200 w/ secret** | 403 | 403 |
| confirm | 403 | 403 | 403 | 403 | **200 (once)** | mint only |
| cancel | 403 | 403 | 403 | **200 (once)** | 403 | mint only |
| `/admin/tokens` | 401 | 401 | 401 | 401 | 401 | **201** |
| `/premium/events` | 402 → 200 w/ payment | 402 → 200 | 402 → 200 | 402 → 200 | 402 → 200 | 402 → 200 |

Intentional public exceptions: read routes, `/access` bootstrap (interim; A2 replaces), premium terms. Tokens are HMAC, domain-separated, subject-bound, single-use where noted, replay-protected via persisted nonces.

## 5. Booking + escrow lifecycle

```
                 POST /book (book token)
                       |  server computes: price = unit_price*qty
                       |  fee = hub_fee_c(price); payout = price - fee
                       v
                   [ HELD ] ------------ cancel (cancel token) --------> [ REFUNDED ]
                       |                                        (inventory restored,
                       |                                         ledger escrow=REFUNDED)
                       | confirm (confirm token, owner side)
                       v
                  [ RELEASED ]  (ledger escrow=RELEASED, released_to=owner)

   free listing (amount = 0): creation ----------------------------> [ WAIVED ]
   (no payment rail; verified-human gate unchanged; conformance C4b:
    WAIVED is only valid at amount 0 — enforced by check_hub.py)

Invalid transitions (all 403/409, verified): confirm→confirm (replay 403),
cancel→cancel (replay 403), cancel after RELEASED (409).
```

Server-owned booking fields (clients CANNOT set): `id` (24-hex random), `escrow`, `amount`, `hub_fee`, `owner_payout`, `created`, `booked_by`, per-vertical projection fields. **Server-owned listing fields:** `owner` = authenticated token principal (anti-spoof: a merchant can never claim another merchant's listings, which `/orders` authorization depends on) and `manage_code_hash` = sha256 of the per-listing manage code (shown ONCE in the create response; knowledge of the code proves ownership for edit/delete — pilot-grade ownership until A2 personhood; never stored in plaintext, never returned again). Client fields (allowlist): events `attendee, quantity`; food `buyer, quantity`. Everything else → 400.

## 6. Identity + delegation

- Adapters (declared in manifest): `midnight-zk-personhood` (flagship, production), `email-or-phone-attestation` (interim stub; booking sets `human_verified: true`), signed capability tokens (this spec's interim bearer layer).
- **Delegation:** the human's agent books AS the principal that holds the book token; `booked_by` records that principal. Production personhood binds `booked_by` to a ZK-verified human credential without revealing identity. Wallets generate personhood proofs on-device; agents only USE the credential (A2 constraint).
- Admin key MUST come from env (`HUB_ADMIN_KEY`); production MUST fail closed on dev defaults (`HUB_ENV=production`).

## 7. Privacy projection (exact public fields)

- **Listings (public):** all client-listed fields + `id`, `registered`/`available`. Owner field is what the merchant chose to publish.
- **Bookings (public/principal view):** `id, listing_id, vertical, escrow, amount, hub_fee, owner_payout, quantity, created, booked_by, attendee/buyer → per-booking random ref (NOT derived from name; NOT linkable across bookings)`. Raw attendee/buyer names NEVER appear in public state or in `state.json` (verified).
- **Ledger (public):** `ts, booking(id), amount, hub_fee, owner_payout, escrow` (+`released_to`/`refunded_to`). No participant identity fields — ever.
- **Private store:** `SECRETS[bid] = {secret, private{real fields}}` — **memory-only**, retrievable ONLY with the booking secret via header. Residual-linkage disclosure: `booked_by` principals are stable per agent; timing correlation is possible; amounts are public by design (auditability) — anyone needing amount-privacy waits for the Midnight shielded rail (A3).

## 8. Payment adapters + capability modes

| Mode label | Meaning |
| --- | --- |
| `SIMULATED` | logic real, money fake (stub verification, labeled in body + manifest) — current for `/premium/events` |
| `TESTNET` | real chain verification/settlement on testnet (C3a target: base-sepolia USDC, EIP-3009) |
| `PRODUCTION` | real settlement, mainnet assets |

- Rail 1 (default, transparent): **x402** — wire format per `payments_x402.md` (402 `accepts[]` → `X-PAYMENT` base64 payload → `X-PAYMENT-RESPONSE`). Verified core: EVM/USDC `exact`. ETH on x402: hypothesis until C3. ADA/FET: custom adapters, NOT x402 core (manifest says so).
- Rail 2 (privacy, designed): **Midnight shielded** (Zswap; hides payer/amount/asset) — GATED Phase A.
- Settlement rule: payTo is merchant-side; facilitator fees are merchant-side; hub takes only its declared hub fee on bookings, never custodies payment assets.

## 9. Ledger accounting model

Event-sourced: one append per escrow transition (booking→HELD; confirm→RELEASED(+released_to); cancel→REFUNDED(+refunded_to)). Totals = computed from events. Integer minor units internally (integer cents); `hub_fee_c(price_c) = price_c * round(FEE_PCT*100) // 10000` (G3 env `HUB_FEE_PCT`, default 1%); refund = full amount to buyer; rounding loss (≤1 unit/booking) accrues to the hub by construction.

## 10. Idempotency semantics

- Optional `Idempotency-Key` header on POST /book. Stored: `{key → {sha256(canonical payload) → response}}` where canonical payload = `json.dumps({"principal": <token sub>, **booking_data}, sort_keys=True)` — **principal-bound**: the principal is part of the hashed payload, so another agent replaying a stolen key gets a hash mismatch → 409, never the original response (which contains secrets).
- Same key + same payload → stored response, `replayed: true` (idempotent across restarts, G1).
- Same key + different payload → **409**.

## 11. Versioning & conformance

- Protocol version in manifest (`agent-hub/0.2`). Additive changes bump minor; breaking bump major.
- A conformant hub MUST: serve this manifest shape; enforce the §4 matrix exactly; project §7 fields exactly; label payment modes truthfully; fail closed in production on dev keys; keep the ledger append-only.
- A conformant client SHOULD: probe `/.well-known/agent-hub.json` first; use header-only credentials; send `Idempotency-Key` on bookings; treat 402 per x402; never log credentials.

## 12. Account email recovery (B3c-email)

- `POST /accounts/email/bind` `{email, account_code | X-Hub-Token}` → 6-char verification code sent. Delivery via `HUB_EMAIL_MODE`: `off` (default, refuse politely) | `log` (dev: code in hub log, chat labels it) | `smtp` (real send; Gmail requires an App Password — account password → SMTP 535).
- `POST /accounts/email/verify` `{email, code}` → `email_verified=true`; recovery enabled. Codes: 6 chars, 15-min TTL, single-use, hashed at rest.
- `POST /accounts/email/recover` `{email}` → ALWAYS the same answer whether or not the email is bound (no account enumeration); rate-limited 5/h.
- `POST /accounts/email/recover/confirm` `{email, code}` → NEW `account_code` shown ONCE; old code invalid immediately (recovery == rotation). All previously issued login tokens are revoked (B1 generation bump).
- Email is optional; accounts without email can still rotate via `/accounts/rotate` with their current code.

### 12a. Crypto accounts (keypair) + PoW cost curves (HARDENING-v2)

> **STATUS: permanent Tier-1 signup** (owner identity model, 2026-09-10). Two tiers: Tier 1 = this
> super-easy signup — always available, registration must never get harder. Tier 2 = Midnight
> wallet/personhood verification (A2) as an optional trust BENEFIT on top (replaces operator vouch).
> Standard primitives only (cryptography lib, RFC 8032 Ed25519 — the key type uAgents use) + thin
> challenge-response glue; PoW cost curves remain the abuse gate.

Accounts have a `kind`: `keypair` (default for signups that send a pubkey) or `code` (legacy fallback).

- **Keypair accounts**: the client generates an Ed25519 seed locally; the hub stores ONLY the 32-byte
  public key. The seed never crosses the wire — a full hub compromise reveals no usable credential.
- `GET /auth/challenge?kind=signup` -> `{algo, challenge, difficulty, ttl}`; client finds a nonce whose
  sha256(challenge+str(nonce)) has `difficulty` leading zero bits.
- `POST /accounts/signup {agent, pubkey?, pow}` -> 201 `{account_id, kind, pubkey?}`. Without `pubkey`,
  a legacy `code` account is created (code shown once, sha256 at rest).
- `GET /auth/challenge?kind=login&pubkey=<hex>` -> single-use login challenge (TTL 120s, one per account).
- `POST /accounts/login {pubkey, agent, sig}` with `sig` = ed25519 signature over
  `b"everlist-login:" + challenge`. Replay impossible: challenges are single-use.
- Recovery rotates the credential: `recover-confirm {email, code, pubkey}` swaps the account pubkey
  (old seed dies); legacy accounts receive a new code. Email bind works with a login token or code.
- **PoW cost curves** are the primary DoS gate: signup 18 bits (~0.3s CPU), recover 16 bits (~0.07s).
  Env-tunable via `HUB_POW_SIGNUP_BITS` / `HUB_POW_RECOVER_BITS`. Challenges single-use, TTL 600s.
- **Per-source fairness**: auth limiters are per source IP (set `HUB_TRUST_PROXY=1` behind a reverse
  proxy to honor X-Forwarded-For). One attacker can no longer lock out signups for everyone.
- Never persisted: seeds, raw codes, email codes (only hashes + pubkeys at rest).
- **B1 token revocation**: every account login token embeds the account's `gen` counter at mint;
  `/accounts/rotate`, `/accounts/email/recover/confirm` (both kinds) and the new
  `POST /accounts/logout-all` (token-authenticated) bump `gen`, instantly killing ALL previously
  issued tokens. The chat command is `logout-all`.

## 13. Listing archive (soft delete)

- `POST /listings/{id}/manage` `{action: "archive" | "unarchive"}` (manage code or account token).
- Archived listings: hidden from `/listings` and `/search`; booking attempts answered `409 listing archived` BEFORE field/payment validation (after the verified-human gate); visible to owners via `GET /listings?archived=1`.
- Existing bookings remain fulfillable. `delete` stays hard removal (refused while bookings HELD/WAIVED).

## 14. Listing ID policy

- IDs are `{vertical[:4]}-{N}` from a persistent per-vertical counter (state `id_counters`), monotonic, NEVER reused after deletes, stable across restarts. Legacy `len(LISTINGS)+1` allocation was removed (collision risk after deletes).

## 15. Public-read sanity limits (B7)

- **Search term cap:** `q` longer than 200 chars is rejected with `400 q too long (max 200 chars)` (applies to `/search`; chat surfaces the same reason honestly).
- **Pagination:** `/listings` and `/search` accept `offset` (default 0, negative→0) and `limit` (default 500, clamped 1..`HUB_READ_PAGE_MAX`, default max 500). Responses add `offset`, `limit`, `returned`; `count` stays the FULL match count (backward compatible: small hubs still get everything in one page).
- **Read backstop:** `/search` and `/listings` are limited per source (same `_source_of` fairness as auth): `HUB_READ_LIMIT` reads/min (default 600 — generous; legit agents never hit it). Over the limit → `429 too many read requests`. Chat search that hits a hub rejection surfaces the REAL reason, never 'unreachable'.

## 16. State backup rotation (B8)

- Before every persist, if the current `state.json` exceeds `HUB_BACKUP_MIN_BYTES` (default 1 MB), the PRE-persist snapshot is copied to `<state_dir>/backups/state-<ns>.json`; only the newest `HUB_BACKUP_KEEP` (default 5) backups are kept (chronological rotation).
- Newest backup therefore always equals the state immediately before the latest write — a bad write or operator error can lose at most one mutation, not the ledger.
- Backup failure degrades to a logged warning; persistence itself never breaks. Env knobs: `HUB_BACKUP_MIN_BYTES`, `HUB_BACKUP_KEEP`.
