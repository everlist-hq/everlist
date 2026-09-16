# EverList Web Buildout Plan — v1.1 (owner-approved)

Created 2026-09-15. **Status: APPROVED by owner 2026-09-15 — work the phases one by one.**
Supersedes nothing; complements docs/backlog-v3.md (hub track) and docs/strategy-boundaries.md (binding).

## 0a. Progress tracker

| Phase | Status | Commit | Date |
| --- | --- | --- | --- |
| W0 hygiene | DONE + verified | 1b0653a | 2026-09-15 |
| W1 accounts + escrow visible | DONE + verified (37-check suite, full gate ALL PASSED, visual money-loop E2E in browser) | 2311e96 | 2026-09-15 |
| W2 discovery + SEO | DONE + verified (62-check suite, SSR detail pages, sitemap live, Caddy fix made durable; CI tests+deploy green on 8e630e9) | 8e630e9 | 2026-09-15 |
| W3 organizer suite | DONE + verified (69-check suite, full gate ALL PASSED; hub self-deadlock found+fixed; booking detail page + JS-PoW signup form still open) | 94f794c | 2026-09-16 |

## 0. Owner directives (2026-09-15, binding — SHIPPED same day)

1. **Chat-first interface, NO manual searching.** The chat IS the search: results are presented as real UI on the board (numbered cards matching "book <n>"), not chat bubbles. Header chips route through the chat as sample searches; no manual filter UI. ✅ shipped (commit 9c22d82)
2. **LLM wired into the chat: mercury 2.5.** Was already configured but token-starved (1200 budget eaten by reasoning → empty replies); fixed to 4000 (EVERLIST_NLU_MAX_TOKENS), verified 4/4 routing probes, boundary intact (mercury routes, never speaks). ✅ shipped
3. **Smart card expansion.** Clicked card grows (spans two columns, description reveals); neighbors are pushed aside with a FLIP glide at their exact size — never shrunk, never distorted; one card at a time; Esc/click-outside closes; reduced-motion respected. ✅ shipped

These directives amend I1: chat is not just "visible on every page" — **it is the only search entry point**; the board renders what the chat finds.

---

## 1. Mission & invariants

Build everlist.network from a chat-plus-grid front page into the **full human face of the hub**, without changing what the hub is.

Binding invariants (owner-pinned, inherited from strategy-boundaries.md):

| # | Invariant | Consequence for this plan |
| --- | --- | --- |
| I1 | **Chat is the main interaction** (owner call, 2026-09-15) | Every page keeps the dock visible; new pages are reachable from the grid + chat, never replace it |
| I2 | **Escrow is the moat** — refund/liveness guarantees never weakened | UI is read-only on escrow state; transitions only via existing single-use token endpoints (/confirm, /cancel); no new money paths |
| I3 | **Two-tier identity law** — Tier 1 signup never gets harder; Tier 2 (Midnight) optional benefit | UI login/signup adds a door, never a gate; verified badges displayed, never required to browse/book |
| I4 | **No coin; stablecoins + escrow; x402 opt-in; Midnight parked/gated** | All payment surfaces carry mode labels (SIMULATED/TESTNET/PRODUCTION); no real-rail UI until owner GO |
| I5 | **Agents are first-class readers/actors** (open protocol moat) | Every human feature has an API-first counterpart; "For agents" page documents the open API; no agent-only capability gets locked behind HTML |
| I6 | **Listing litmus test** — a date where a human is needed/wanted; offers AND seeks | Discovery UI treats offer/seek as first-class; jobs vertical (agent-posted human work) gets a real surface in its phase |
| I7 | **CSP-safe rendering contract** — no inline handlers, textContent-only for dynamic data | Carried into every new component; regression-tested per phase |
| I8 | **2GB VPS, no heavy stack** | Vanilla JS + tiny server-rendered HTML in webchat.py; no node build step, no framework runtime |
| I9 | **Honesty labels** (SIMULATED etc.) and demo-data hygiene | Placeholder demo fixtures cleared before public-facing expansion; every simulated surface labeled |

---

## 2. Where we are (verified inventory, 2026-09-15)

### 2.1 Hub capabilities (app.py — the engine, all live and tested)

Accounts: signup (PoW + Ed25519 keypair), login (account_code), /accounts/me, rotate, logout-all, email bind/verify/recover, payout settings, vouch (admin), verify-midnight (gated).
Listings: create, fetch, manage (edit/archive/delete, schema-derived allowlist), server-owned fields, weighted ratings (rating_wsum/wtot, review integrity L4, burst detection).
Bookings: create (escrow HELD, idempotency), list own, merchant orders, get w/ secret, confirm (single-use), cancel (single-use), escrow chain-sync (M7/M8 verified).
Discovery: /verticals, /listings, /search (FTS5), /suggest, /registry (open hub network), /ledger (append-only, pseudonymous), manifest + OpenAPI.
Admin: tokens, review-queue, sync-escrow.
Gated: /premium/events (x402), Midnight verify.

### 2.2 Web surface today (webchat.py + static/)

- Direction A layout (owner-approved): centered chat dock + minimize pill, Discover grid with real listings, date blocks, escrow badge, category chips (All/Events/Services/Classes), one-message posting via chat, NLU intent router (mercury, fail-open).
- Original palette (#0d1117 + #4ade80) + new EL logo (favicon.svg embedded + logo-512.png).
- /api/* passthrough to hub.

### 2.3 The gap (what humans cannot do on the site today)

No login UI, no my-bookings, no merchant inbox, no listing detail pages, no edit forms, no ratings display, no ledger/transparency view, no how-it-works, no SEO surface, no agent-facing docs page. All of it exists in the API — the site just doesn't show it.

---

## 3. Architecture decisions (proposals — owner review requested)

| Decision | Proposal | Why / alternative considered |
| --- | --- | --- |
| D1: Split of duties | **hub (app.py) stays pure JSON protocol for agents, forever (SPEC §1). webchat.py becomes the human-side server**: static assets + a few tiny server-rendered HTML endpoints + passthrough | Keeps protocol clean; human UX never pollutes agent contract. Alternative (render HTML in hub) violates SPEC §1 |
| D2: Frontend stack | Vanilla JS ES-modules, no build step, shared `api.js` client + `ui.js` components | I8; matches current codebase; zero supply-chain risk. Alpine considered (A0 uses it) but adds a runtime for little gain at this scale |
| D3: Listing detail pages | **Server-rendered minimal HTML in webchat.py** (`/l/{id}`) with OG tags + JSON-LD schema.org, hydrating to interactive page via JS | SEO requires server HTML; JS-only pages don't rank. URL `/l/{id}` avoids any confusion with hub `/listings` |
| D4: Auth in UI | W1: **login-only form** (account_code + agent name → 24h tokens, PoW not needed on login). Signup stays chat-first (proven one-message flow, I3); W3 adds a form fallback with JS-PoW in a Web Worker | Login is cheap and safe now; PoW-in-browser is the only fiddly part and can wait |
| D5: Token storage | localStorage + prominent Sign out + logout-all button; revisit httpOnly-cookie sessions post-pilot | Acceptable for pilot given CSP discipline (I7); simplest path |
| D6: Routing/Caddy | Extend the @chat matcher in deploy.sh with the new webchat paths (exact list in §9) | Caddy path-router is the established mechanism; hub paths untouched |
| D7: Escrow visualization | One shared **escrow-timeline component** (HELD → RELEASED/REFUNDED + refund-window countdown per §19) used in chat cards, booking detail, dashboard | Single source of visual truth for the moat |
| D8: Email | Defer all outbound email until owner provides SMTP creds (recovery currently logs only) | No creds on server today; feature is cheap once available |
| D9: Analytics | Tiny self-hosted counter in webchat (JSON file, page hits, no cookies, no PII) | I8/I9; Plausible/Umami considered but heavier; revisit if growth demands |

---

## 4. Feature map (everything we will need, mapped to phases)

Legend: ✅ live · 🟡 planned here · 🔒 gated (owner GO required) · ⏳ later/optional

### 4.1 Visitor / discovery

| Feature | Today | Phase |
| --- | --- | --- |
| Browse grid, real data | ✅ | — |
| Category chips (static) | ✅ → taxonomy-driven from /verticals | W2 |
| Filters: vertical, offer/seek, date range, price, location text, has-escrow | — | W2 |
| Full-text search page (FTS5 facets, shareable URLs) | chat only | W2 |
| Listing detail page `/l/{id}` (SSR + OG + JSON-LD + ICS) | — | W2 |
| Calendar view of events (month strip) | — | W2 |
| How-it-works / escrow explainer / FAQ | — | W5 |
| "Book with your agent" copy-snippet on every listing (agent-skill instructions) | — | W5 |
| Maps/geocoding | — | ⏳ 🔒 external dependency |
| i18n | — | ⏳ |

### 4.2 Account holder

| Feature | Today | Phase |
| --- | --- | --- |
| Login form (account_code) → tokens in UI | chat only | W1 |
| Dashboard: my bookings (status, escrow timeline, cancel) | chat only | W1 |
| Account settings: whoami, rotate, logout-all, email bind/recover, payout settings | chat only | W1 |
| Signup form (JS-PoW worker) as fallback to chat signup | ✅ W3b: in-browser keygen (tweetnacl, seed never leaves device) + PoW, stronger than chat path | — |
| Booking detail `/booking/{id}` (session-token gated page, escrow timeline) | ✅ W3b | — |
| Tier-2 Midnight verified badge display | 🔒 | W7 🔒 |

### 4.3 Organizer / merchant

| Feature | Today | Phase |
| --- | --- | --- |
| One-message listing via chat | ✅ | — |
| Post wizard form (vertical-aware fields, live schema from /verticals, /suggest tags) with handoff to chat | — | W3 |
| Manage listings: edit (schema-derived allowlist), archive/unarchive, delete (refused while HELD — surfaced honestly) | chat only | W3 |
| Booking inbox (orders) + confirm button | chat only | W1 |
| Payment-terms fields on listing (per SPEC §12/§19: rail, refund window, deposit) | partial | W3 |
| Capacity/availability view | capacity shown | W3 |
| Organizer public page `/org/{account}` (their live listings) | — | W5 ⏳ |

### 4.4 Trust & transparency (the moat, made visible)

| Feature | Today | Phase |
| --- | --- | --- |
| Escrow timeline component (chat + pages) | badge only | W1 |
| Ratings display: weighted average + verified-booking reviews on cards/detail | ✅ W4 shown: SSR detail line + board-card line (paid = amount-weighted, free-class separate channel); zero reviews → no fake stars; integrity internals stay server-only | — |
| /ledger transparency page (append-only, pseudonymous) | ✅ W4: `/transparency` — totals + full entries table, honest zero state, agents-note points at GET /ledger | — |
| /registry network page (open hub federation) | ✅ W4: `/network` — tiers + hubs + responsibility verbatim from GET /registry | — |
| Vouch/verified badge display | ✅ W4 (honest scope): registry-tier badge on /network only — hub deliberately keeps per-owner verification server-side (provenance law) | — |
| Report/flag listing (routes into admin review-queue via chat command first) | deferred: hub has no report endpoint — a UI button would be a dead promise; needs hub-side `/report` first (owner call) | W5? |

### 4.5 Chat upgrades (stays the main window, gets stronger)

| Feature | Phase |
| --- | --- |
| Auth-aware chat (logged-in users: "book it" needs no re-auth) | W1 |
| Richer cards in transcript (reuse escrow timeline + book/cancel actions) | W1 |
| Chat-driven post flow polished (vertical detection, missing-field prompts, preview before create) | W3 |
| Deep links from chat into detail/dashboard pages | W2 |
| Streaming responses | ⏳ (webchat is stdlib; SSE possible later) |

### 4.6 Agents / protocol surface

| Feature | Phase |
| --- | --- |
| "For agents" page: open API doc, manifest link, skill guide, copy-paste flows | W5 |
| MCP server endpoint | 🔒 W7 (owner GO) |
| Registry federation dashboards | W4 (read-only page) |

### 4.7 Platform / ops / growth

| Feature | Phase |
| --- | --- |
| Caddy route expansion + Cache-Control for static + slim favicon headers | W0/W2 |
| Demo-fixture hygiene gate (no example.com/Bad Tatzmannsdorf on public pages) | W0 |
| Sitemap.xml + robots.txt (webchat-generated from live listings) | W2 |
| PWA manifest + icons (installable, logo) | W5 |
| Accessibility pass (focus, contrast, reduced-motion, labels) | W5 |
| Light/dark toggle (palette tokens already proven by A4/A6) | W5 |
| Self-hosted hit counter | W2 |
| Status page (health + escrow-sync freshness) | W5 ⏳ |
| Email notifications (booking events, digest) | 🔒 W6 (SMTP creds) |
| Disputes UI beyond cancel/confirm | 🔒 ⏳ needs owner policy call |
| Real payment rails in UI (x402 PRODUCTION) | 🔒 W7 |
| Jobs vertical (agent-posted seeks for humans) full surface | W4 schema draft → W6 UI 🔒 |
| Rides / marketplace-with-dates verticals | ⏳ schema governance |
| Mobile native app | ⏳ (PWA first) |

---

## 5. Phases, tasks, done-when

Every phase: baseline `make test` green → build → new pytest coverage → gate green → screenshot-verified (vision check, the standing lesson) → sync staging → deploy via established key-SSH path → live curl verification → worklog.

### W0 — Hygiene (0.5 session)
- Demo-fixture audit: no placeholder URLs/venues reachable from public UI; either clean or clearly mark demo listings.
- Static caching headers + favicon cache; 404/error page.
- Done-when: `curl` shows Cache-Control; demo audit checklist in worklog; gate green.

### W1 — Accounts, dashboard, escrow made visible (2–3 sessions)
1. `api.js` shared client (fetch wrapper, token storage, error envelopes) + `ui.js` (toast, modal, timeline, form field) — CSP-safe.
2. Login view (`/account`): account_code + agent → tokens; whoami; sign out; logout-all.
3. Dashboard `/dashboard`: My bookings (from /bookings) with escrow timeline + cancel (single-use token flow); Booking inbox (from /orders) with confirm.
4. Account settings: rotate, email bind/recover status, payout settings.
5. Escrow-timeline component reused in chat cards; auth-aware chat.
6. Caddy/deploy.sh route additions for /account, /dashboard, /assets/*.
- Tests: test_webchat extension (auth flow, dashboard passthroughs, timeline rendering contract); negative: expired/foreign token → honest 401/403 surfaces.
- Done-when: full login → book (chat) → see booking in dashboard → cancel → REFUNDED visible, proven on render stack + gate green.

### W2 — Discovery depth + SEO (2 sessions)
1. Taxonomy-driven filters from /verticals (vertical, category, offer/seek, date, price cap, location text).
2. Search page with shareable query URLs; pagination (B7 caps respected).
3. `/l/{id}` SSR page: server-rendered HTML with OG + JSON-LD (Event/Service/FoodEstablishment as appropriate) + ICS download + Book/Ask-AI CTAs; hydrates to interactive.
4. sitemap.xml + robots.txt generated from live listings; hit counter.
5. Chat deep-links into /l/{id}.
- Done-when: curl /l/{id} returns server HTML with JSON-LD for every live listing; sitemap lists them; mobile + desktop screenshot-verified; gate green.

### W3 — Organizer suite (2–3 sessions)
1. Post wizard `/post`: vertical-aware form (schema from /verticals), /suggest tag chips, payment-terms fields (§19: rail, refund window, deposit), preview → create; handoff option to chat at any step.
2. Manage UI: edit (schema-derived allowlist exactly), archive/unarchive, delete with honest refusal messaging when bookings HELD.
3. Booking detail page with secret-gated access.
4. JS-PoW signup form fallback (Web Worker) — chat signup stays the default door (I3).
- Done-when: create → edit → archive → delete cycle fully in UI proven on render stack; suite green.

### W4 — Trust & transparency (1–2 sessions)
1. Ratings display (weighted avg, count, recent verified-booking reviews) on cards + /l/{id}; integrity internals stay server-only (never leak flags).
2. `/transparency` (ledger page): append-only, pseudonymous, explains fee fairness.
3. `/network` (registry page): open hub list + checker status.
4. Verified/vouch badge display; report button → admin review-queue path (chat command fallback first).
5. Jobs vertical schema draft (offers/seeks, agent-posted seeks carry chat provenance note) — **owner review gate before implementation**.
- Done-when: a booking→rate→display loop proven end-to-end; ledger/registry pages match API byte-for-byte.

### W5 — Growth, polish, agent-face (1–2 sessions)
1. How-it-works + FAQ + escrow explainer (honest: what escrow does/doesn't cover).
2. "For agents" page: manifest, OpenAPI link, skill guide, copy-paste booking flows.
3. PWA manifest + icons; light/dark toggle; accessibility pass; reduced-motion.
4. OG share images per vertical; landing copy tightened for organizer outreach (P1 support).
- Done-when: Lighthouse-style manual checks (no new heavy tooling) documented; gate green.

### W6 — Comms & jobs UI (gated)
- 🔒 Email notifications (SMTP creds needed): booking created/confirmed/cancelled/refunded, weekly organizer digest.
- 🔒 Jobs vertical UI (post-W4 schema approval).

### W7 — Gated future (owner GO each)
- 🔒 x402 PRODUCTION surfaces; 🔒 Midnight Tier-2 badge + verify flow in UI; 🔒 MCP server; ⏳ disputes UI policy; ⏳ maps; ⏳ i18n; ⏳ org pages; ⏳ native app.

---

## 6. Cross-cutting workstreams

- **Design system**: tokens already in style.css; component CSS grows in one file per component; every component textContent-only, focus-visible, reduced-motion aware.
- **Testing discipline**: pytest per feature (test_webchat grows; new test_pages.py for SSR/SEO); negative tests for every auth surface; CSP regression check (no inline handlers/styles creep in); screenshot-verify loop every phase.
- **Deploy discipline**: Caddy matcher updated in deploy.sh (permanent), pull + restart webchat only (hub untouched), on-box `git log` + live byte markers as proof (worklog lesson: 'active' ≠ deployed).
- **Docs**: SPEC untouched (hub contract); new docs/web-ui-spec.md capturing routes/components once W1 lands.
- **Lessons**: every scar into docs/lessons.md.

## 7. Explicitly NOT doing (without an owner call)

No own coin/payment token. No weakening or UI-bypass of escrow refund/liveness. No agent directory (FET boundary). No custom auth crypto expansion. No heavy frameworks/build steps. No real-money rails, Midnight, or MCP without explicit GO. No astroturfed growth features (fake activity, dark patterns).

## 8. Risks & mitigations

| Risk | Mitigation |
| --- | --- |
| Scope creep delays P1 pilot (the real bottleneck per strategy §7) | W0–W2 before/alongside recruitment; W3+ opportunistic; pilot never blocked on UI |
| XSS via richer UI | I7 CSP contract + pytest regression + no innerHTML with dynamic data, ever |
| Token theft from localStorage | Short-lived 24h tokens (already), logout-all prominent, revisit post-pilot (D5) |
| Caddy route mistakes take hub paths down | Additive matcher entries only; deploy.sh diff reviewed; live curl matrix after every deploy |
| 2GB RAM pressure | No new services; SSR is string templates; counter is a JSON file |
| SEO on dynamic data | SSR for the only pages that need ranking (/l/{id}); rest is app-like, fine |

## 9. Route map (Caddy @chat additions, all → webchat :8804)

`/assets/*` · `/account` · `/dashboard` · `/post` · `/l/*` · `/booking/*` · `/transparency` · `/network` · `/how-it-works` · `/agents` · `/faq` · `/sitemap.xml` · `/robots.txt` · `/ics/*` · `/manifest.webmanifest`

Hub keeps: `/search`, `/book`, `/listings`, `/ledger`, `/registry`, `/verticals`, `/.well-known/agent-hub.json`, `/openapi.json`, `/accounts/*`, `/admin/*`, `/access`, `/suggest`, `/premium/*` (unchanged matcher fall-through).

## 10. Open questions for owner (numbered for easy reply)

1. D3: tiny SSR in webchat for `/l/{id}` + SEO — approved?
2. D4/D5: login-only UI auth in W1 (signup stays chat-first until W3) and localStorage tokens — approved?
3. D8: provide SMTP creds now so W6 notifications land early, or defer?
4. D9: self-hosted no-cookie hit counter OK? (or prefer nothing)
5. W4: want the jobs-vertical schema draft early (it's the north-star use case) or keep strict sequence?
6. Deploy authority: confirm the delegated pattern (I push to origin + ssh-pull to VPS) remains authorized for this buildout.
7. Any feature above you want re-prioritized or killed before W1 starts?

## 11. Total effort estimate

~8–12 focused sessions for W0–W5 (the ungated buildout), each session ending gate-green and deployable. Gated phases (W6/W7) sized separately after owner GO.