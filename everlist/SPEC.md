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
| GET | `/openapi.json` | none | 200 | machine-readable API contract (OpenAPI 3.1); linked from manifest `api_contract` |
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
| POST | `/listings/{id}/manage` | list token; account owner (`sub=acct-` == `owner`) OR `manage_code` | 200 / 401 no token / 403 wrong code or not owner / 409 active bookings | edit allowlist SCHEMA-DERIVED (base `title, description, price, location, date, capacity, category, tags, url` + the vertical's own fields — services adds `provider`, `duration_minutes`; food adds `merchant`, `preparation_minutes`); `vertical`/`owner` NEVER editable; capacity ≥ registered; delete refused while bookings are HELD/WAIVED |
| POST | `/book` | book token + optional `Idempotency-Key` | 201 / 201(replayed) / 409 conflict | escrow HELD on creation; **WAIVED when amount = 0**; human proof: verified account (server-side) OR client `human_verified` stub (strict boolean); identity field = bounded nonempty string (≤80 chars) |
| POST | `/accounts/verify-midnight` | account token | 200 Tier-2 verified | requires `credential_id` (positive int); binding 1:1 account↔credential; server-side `verified_by` field set; fail-closed 502 if verifier down |
| POST | `/book/{id}/confirm` | admin-minted confirm token (single-use) | 200 RELEASED | owner-side fulfillment confirmation; H15: also accepts WAIVED (free listings) |
| POST | `/book/{id}/cancel` | booking cancel token (single-use) | 200 REFUNDED | restores inventory, refunds; H15: also valid on WAIVED (free listings — no money moves) |
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

   H15: WAIVED bookings are first-class lifecycle participants —
   confirm (owner side) ----------------------------> [ RELEASED ]
   cancel (cancel token)  ----------------------------> [ REFUNDED ]
   (no money moves in either transition — amount stays 0, C4b
    still passes; inventory restored on cancel exactly as HELD)

Invalid transitions (all 403/409, verified): confirm→confirm (replay 403),
cancel→cancel (replay 403), cancel after RELEASED (409).
```

Server-owned booking fields (clients CANNOT set): `id` (24-hex random), `escrow`, `amount`, `hub_fee`, `owner_payout`, `created`, `booked_by`, per-vertical projection fields. **Server-owned listing fields:** `owner` = authenticated token principal (anti-spoof: a merchant can never claim another merchant's listings, which `/orders` authorization depends on) and `manage_code_hash` = sha256 of the per-listing manage code (shown ONCE in the create response; knowledge of the code proves ownership for edit/delete — pilot-grade ownership until A2 personhood; never stored in plaintext, never returned again). Client fields (booking allowlist, DERIVED from the vertical schemas — adding a vertical extends it automatically): events `attendee, quantity`; food `buyer, quantity`; services `client, quantity`. Everything else → 400.

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

### Write-path backstops (H5)

Every mutating POST route carries a per-source fixed-window backstop (`_auth_allow`, same fairness as the auth kinds; counts EVERY attempt — wrong manage-codes and wrong admin keys included, so brute-force dies at the limit, not at the secret):

| Kind | Route(s) | Default | Env | Window |
| --- | --- | --- | --- | --- |
| `signup` | `/accounts/signup` | 30/h + PoW | — | 3600s |
| `login` | `/accounts/login` | 120 | — | 60s |
| `rotate` | `/accounts/rotate` | 10/h | — | 3600s |
| `email`/`verify`/`recover` | `/accounts/email/*` | 5/20/10 per h | — | 3600s |
| `read` | `/search`, `/listings` GET | 600/min | `HUB_READ_LIMIT` | 60s |
| `access` | `/access` | 60/min | `HUB_LIMIT_ACCESS` | 60s |
| `create` | `POST /listings` | 60/min | `HUB_LIMIT_CREATE` | 60s |
| `book` | `/book` | 60/min | `HUB_LIMIT_BOOK` | 60s (plus G4 per-principal 10/min) |
| `manage` | `/listings/{id}/manage` | 60/min | `HUB_LIMIT_MANAGE` | 60s |
| `admin` | `/accounts/vouch`, `/admin/tokens` | 30/min | `HUB_LIMIT_ADMIN` | 60s |

- Defaults sit far above measured legit volume (the full test pipeline peaks ≈25 writes/min from one source) — only abusers ever feel them.
- `/accounts/logout-all` is intentionally unlimited: each SUCCESS bumps the account `gen` and revokes the calling token, so a flood is one success plus harmless 401s.
- Fairness is per source: a flooded source gets 429 while fresh sources keep working — never a global lockout.

## 16. State backup rotation (B8)

- Before every persist, if the current `state.json` exceeds `HUB_BACKUP_MIN_BYTES` (default 1 MB), the PRE-persist snapshot is copied to `<state_dir>/backups/state-<ns>.json`; only the newest `HUB_BACKUP_KEEP` (default 5) backups are kept (chronological rotation).
- Newest backup therefore always equals the state immediately before the latest write — a bad write or operator error can lose at most one mutation, not the ledger.
- Backup failure degrades to a logged warning; persistence itself never breaks. Env knobs: `HUB_BACKUP_MIN_BYTES`, `HUB_BACKUP_KEEP`.

## 17. Dependency hygiene (H6)

- All direct dependencies in `requirements.txt` are pinned to exact versions (`uagents`, `httpx`, `eth-account`, `cryptography`) — upgrades are deliberate, audited events.
- `make audit` runs `pip-audit` over the full resolved dependency tree; CI runs the same audit (non-blocking until the tree is fully clean upstream).
- **2026-09-11 re-audit (S5):** pip check clean; pip-audit re-confirmed exactly the two waived findings ( §18 table) — pynacl fixed 1.6.2 exists but cosmpy pins ==1.6.0 (empirically upgrade breaks pip check), ecdsa still has no upstream fix. Waivers stand.

## 18. Privacy & data (H8)

- Authoritative, exact description of stored data, retention, and deletion: see [PRIVACY.md](PRIVACY.md).
- Summary: codes/seeds stored as SHA-256 hashes only (keypair accounts: public key only — the seed never leaves the device); recovery email stored in plaintext (required for delivery) and erased by account deletion (§12a path: `DELETE /accounts/me`); the public ledger is pseudonymous; limiter/session/challenge data is memory-only.
- Honest limitation: pre-persist backups (§16) keep up to 5 snapshots — deleted records can linger in older generations until rotation removes them.
- Known vulnerabilities, explicitly waived (re-evaluate on every dependency bump):

| ID | Package | Why waived |
| --- | --- | --- |
| PYSEC-2026-3002 (CVE-2025-69277) | pynacl 1.6.0 (transitive via cosmpy) | Vulnerability is in bundled libsodium's `crypto_core_ed25519_is_valid_point` with untrusted data ("atypical use cases"); that API is not reachable through our stack, and cosmpy 0.12.2 pins `pynacl==1.6.0` exactly — the fixed 1.6.2 would break the declared tree. EverList's own crypto is Ed25519 via `cryptography` 50.0.1, not PyNaCl. Re-audit when cosmpy lifts the pin. |
| PYSEC-2026-1325 | ecdsa 0.19.2 (transitive via cosmpy, uagents-core) | Minerva timing attack on P-256 ECDSA *signing*; no fixed version exists upstream. Our own code never uses ecdsa (Ed25519 via `cryptography`); ecdsa signing occurs only inside Fetch agent identity registration — rare, local, non-adversarial. Monitor upstream. |

### 18. Organizer payout keys (M9)

`POST /accounts/payout {payout_pk}` (login token OR account_code proves control): registers the organizer's Midnight **coin PUBLIC key** (64-hex, 32 bytes) on the account. The hub stores **public keys only** — secret keys and seeds are structurally rejected (format wall) and never needed: escrow release/refund pays the coin key fixed at contract creation; the payout key tells agents/the hub where future escrows should point. Replace anytime (previous key dies); chat: `set-payout <64-hex>`. Legacy accounts migrate via the load-time field migration.

## 19. Payment terms policy (owner-pinned, 2026-09-12)

Binding product policy agreed with the owner. Implementation items: C11, C12, M16, M17 (backlog-v3).

### 19a. Agreement semantics

- The merchant sets **payment terms on the listing**: rail (escrow | instant), refund window, deposit requirement (amount), price.
- **Booking = agreement.** The API enforces that a booking is created against the terms attached to the listing; agents see terms before booking (manifest + listing payload).
- **Changes after booking require both parties.** No unilateral rewrite: merchant courtesy refunds are voluntary; terms changes create a new offer the buyer must accept.

### 19b. Rails

| Rail | Default? | Use |
| --- | --- | --- |
| **Midnight escrow** | **Default whenever real money attaches** | events, marketplace, jobs, services — anything where wrong-delivery/no-show matters |
| x402 instant | merchant opt-in per listing | small amounts, trusted repeat customers, digital fulfillment; no refund window (that is the trade-off) |

### 19c. Refund windows (defaults, merchant may override per listing)

| Vertical | Default window |
| --- | --- |
| events | event end + 72h |
| services / gigs | fulfillment + 72h |
| marketplace | delivery confirmation + 7 days |

Window mechanics are fully built: **M4 permissionless `timeoutRefund`** (buyer-favoring: before the refund deadline the buyer can refund themselves; after it, anyone can trigger the refund to the buyer's stored key) plus **M17 `autoRelease`** (merchant-favoring complement, 2026-09-13): every escrow carries a second, later **claim deadline** (set at creation, asserted strictly after the refund deadline). Before the claim deadline the buyer-favoring refund paths apply; after it, `refundEscrow` and `timeoutRefund` are walled and ANYONE can trigger `autoRelease`, which spends the deposit to the merchant's stored key — a buyer who received the service can no longer claw back, and the payout destination is fixed at creation so no caller can redirect it. Verified: 40/40 offline circuit tests, native ZK proof on proof-server 9.0.0-rc.7, mirror paths m7 25/25 + m8 17/17.

### 19d. Deposits and buyer gating

- **Deposit = the standard merchant lever** (backlog C12 flag): a required deposit is just escrow engaged before fulfillment; merchant sets the amount on the listing.
- **Tier-2 buyer gating** (`require_verified_buyer`, backlog C11): premium flag for high-value listings. The identity machinery is fully built (M13/M14); only the listing flag + booking-time check remain. Deferred until Tier-2 is live on the production server.

### 19e. Post-settlement undo

Mutual refund after settlement is implemented (M16, 2026-09-13): the `mutualRefund` circuit requires BOTH role commitments to prove, the merchant returns an equivalent coin through the contract (exact amount + asset asserted; fresh nonce — the original coin was consumed at release), and funds route atomically to the buyer's stored key — neither party alone can move anything. Verified: 32/32 offline circuit tests, native ZK proof on proof-server 9.0.0-rc.7, and the C7 mirror carve-out (chain REFUNDED + hub RELEASED mirrors forward; the reverse is unreachable by any circuit and stays refused).

## 20. Private deals (P2, owner-approved 2026-09-13)

**EverList as the middleman for private transactions.** Any two parties (secondhand sale, freelance
job, ticket resale) get escrow protection without building it themselves: one party creates a
**private listing** and sends the other a claim code; booking locks the money in escrow; release/
refund follows the normal contract rules. The hub never sees secrets; settlement needs no operator.

### 20a. Visibility semantics (owner-corrected naming)

- **`public`** — the default: listed and discoverable (search, suggest, browse, direct GET).
- **`private`** — **listed, not public**: exists and is directly accessible to whoever holds its
  one-time **claim code**, but never appears in `/search`, `/listings` (public branch), `/suggest`,
  or any discovery surface. Private is NOT archived (owner can still manage/book it; existing flows
  keep working; it becomes discoverable again via `make_public`).
- Unknown values are rejected (400). No other visibility values exist.

### 20b. Claim codes

- Minted server-side (`pvt-` + 16 hex, 64-bit), **shown ONCE** at creation, stored as sha256 only
  (same anti-spoof pattern as manage_code; `claim_code_hash` is a server-only field and a reserved
  name community schemas cannot declare).
- Accepted via `X-Claim-Code` header or `?claim=` on GET, and via the booking field `claim`.
- **No existence oracle:** GET/booking of a private listing without a valid claim returns the exact
  unknown-id answer (404 `no listing <id>`) — probing sequential ids learns nothing.
- Owner auth (token principal == listing owner) always passes. Claim codes are consumed by use,
  never stored in plaintext, never echoed in any response.
- Rotation: `manage action=make_private` mints a FRESH claim (the old one dies immediately);
  `manage action=make_public` deletes the hash and re-lists the deal publicly.

### 20c. p2p vertical

Community schema `schemas/p2p.json` (fail-closed loader): required title/price/date/location;
categories secondhand/freelance/tickets/custom; booking identity `buyer` (fields buyer, notes,
quantity). Works on every hub that ships the schema; hubs without it just reject p2p listings
(chat falls back to public `list`).

### 20d. Chat flow (agent path)

- `deal <title> | <price> | <date> | <location> | [category]` (or rich format, incl. C12 keys
  rail/refund_window/deposit) -> returns listing id + one-time claim code.
- Share = id + claim code. Receiver: `book <id> <pvt-claim> <name>` in chat (free deals book
  in-chat; paid deals get SDK guidance including the claim), or SDK booking with `claim` field.
- Share page (`/l/<id>` human-clickable) is planned post-VPS (plan B, deferred).

### 20e. Policy

- Escrow rail is the default for private deals; instant (x402) is the explicit opt-in for trusted
  micro-deals (C12 semantics apply unchanged).
- The hub escrows **money**, not goods: correctness of the traded item remains the parties'
  business; escrow removes the who-pays-first problem and enforces the refund window.
- Real money stays gated behind the existing real-money gate (owner approval / C10 conditions).

## 21. Review integrity (S6, owner-approved 2026-09-13; REQUIRED before real money)

Ratings are the hub's trust surface; fake-review farming is treated as an economic
attack, not a moderation nuisance. Four layers, all server-enforced:

### 21a. L1 — self-review walls

- The listing owner's principal can never rate their own listing (403 `self-review rejected`).
- Instant-rail (DIRECT) paid bookings may bind payment evidence: an optional `X-PAYMENT`
  header carrying a real EIP-3009 `TransferWithAuthorization` is verified server-side
  (x402verify: signature recovery + nonce replay wall) against the listing's
  `receive_addr` (merchant wallet, validated 0x+40hex; optional, falls back to the hub
  pay-to). A booking paid from the merchant's own receive wallet can never rate (403).
  Payment evidence fields (`payment_payer/value/nonce`) are server-only, never
  client-claimable (reserved-field wall).

### 21b. L2 — channel separation

Free (WAIVED) bookings rate into a separate **free-class feedback** channel
(`free_rating_sum` / `free_rating_count`); they never enter the paid aggregate.
The cheapest farm (zero-cost bookings) produces zero paid reputation.

### 21c. L3 — amount weighting

Paid aggregates are weighted by settled amount, capped at weight 50 (weight =
booking amount, floor 1). `rating_avg` is the weighted average; raw `rating_sum` /
`rating_count` remain for auditability. A self-loop of micro-payments cannot buy a
reputation a real booking would earn.

### 21d. L4 — interlock detection (detection only, never auto-delete)

Server-side heuristics over booking payment evidence append to `review_flags`
(server-only, bounded): repeated payer wallet on one listing, payer interlock with a
sibling listing of the same owner, burst ratings (3+ paid within 10 minutes). The
operator reviews via `GET /admin/review-queue` (admin key). Nothing is auto-deleted.

### 21e. Migration & honesty

At boot, aggregates are recomputed from booking history (channel split + weighting),
so pre-S6 ratings aggregate under the new rules too. Aggregates stay absent from the
ledger; ratings carry no identity data. Legacy plain averages are superseded by
`rating_avg` (weighted).

## 22. Verified-buyer gating (C11, owner-approved 2026-09-13)

Merchants can restrict a listing to Tier-2-verified buyers (the M14 machinery
wired as a product lever). The gate is the strongest trust lever EverList offers
and the merchant's explicit choice per listing — never a platform-wide lock.

### 22a. The flag

`require_verified_buyer` is an optional listing field, strict boolean (400 on
anything else, `null` treated as unset). It is part of the public listing face
(`_pub_listing` passes it through): buyers see the requirement BEFORE booking.

### 22b. Enforcement is server-side only

`POST /book` on a gated listing checks the ALREADY-computed server-side
`acct_verified` (account principal + `human_verified` from midnight-zk /
admin-vouch). The client-asserted `human_verified` stub NEVER satisfies the
gate. The 403 names the requirement and the fix (`/accounts/verify-midnight`).
Provenance flows through: the booking stores the server-derived `verified_by`.

### 22c. Owner control

Settable at creation and via manage `edit` pre-booking (strict-boolean wall,
`require_verified_buyer` listed in the editable-set error). Non-owners cannot
flip it (403). Gate flip-off then re-arm is tested; post-booking flips never
rewrite existing bookings.

### 22d. Chat

Rich format key `verified_only: yes|no` (case-insensitive, `true`/`false`
accepted); junk values refuse with guidance instead of silently flipping the
gate. `_fmt_listing` shows a verified-buyers-only line for gated listings.

### 22e. Composition with private deals (P2)

Claim code and verification are INDEPENDENT walls on a private gated deal:
valid claim without verification is still 403; verification without claim is
still 404. Neither substitutes for the other.
