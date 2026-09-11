# Implementation Plan — Vertical Expansion & Notifications

Spec: `docs/2026-09-11-vertical-expansion-design.md` (approved 2026-09-11).
Owner decisions locked in: SQLite upgrade approved (supersedes H19's data-gate), chat UX is the product, webpage last, links-not-media, three-leg notifications, ship all four new verticals together.

## Standing rules (unchanged)

Baseline green before each phase · one task at a time · every task ends with its done-when proven by a permanent test · never push (owner pushes) · chat-help honesty regression after every chat change · no heredocs for code.

## Phase 1 — Foundation: SQLite + dynamic schema registry

| # | Task | Done-when |
| --- | --- | --- |
| 1.1 | SQLite persistence adapter behind the existing storage interface (state.json stays as export/import format) | hub restarts with zero data loss on a 10k-post fixture; all existing suites pass unchanged |
| 1.2 | FTS5 full-text index + indexes on `(vertical, city, date)`, `tags` | 10k-post fixture: search + browse queries < 50 ms (benchmarked in test) |
| 1.3 | Vertical registry moves from code to data: shipped schemas load from `schemas/*.json`, admin can register a new vertical at runtime (`POST /admin/verticals`) | new vertical registered at runtime is immediately usable end-to-end (create, book, search) without restart |

## Phase 2 — Generalized post model

| # | Task | Done-when |
| --- | --- | --- |
| 2.1 | `role` field (`offer`/`seek`) on all posts; seek posts are bookable/claimable by the other side | seek + offer flows E2E for one vertical |
| 2.2 | Structured location: `city` (indexed, required unless `online`), `country`, `venue`, `online` flag, reserved `lat/lon` | `browse events in <city>` = indexed query; posting without city/online is refused with a helpful chat prompt |
| 2.3 | Tags: user tags + auto-extraction from title/description (normalized, lowercase, deduped); tag filter first-class in search/browse | "Surya Kriya" findable via `yoga`, `meditation`, and name |
| 2.4 | Expiry: `ends`/`deadline` per vertical; auto-expire worker hides expired from default browse/search; owners see archive | expired post invisible in browse, visible to owner, restorable |
| 2.5 | Price semantics: `fixed / free / donation / negotiable` (+ salary range for jobs) | each variant posts and displays correctly; free still books WAIVED |
| 2.6 | Status lifecycle: `open → paused → closed` (owner-controlled) on top of auto-expiry | pause hides from browse; close rejects new bookings with clear reason |

## Phase 3 — New verticals (ship together)

| # | Task | Done-when |
| --- | --- | --- |
| 3.1 | `jobs` schema: pay/workload/workplace(remote-onsite-hybrid)/apply-by; **apply = book** (applicant details in private envelope), organizer confirm = shortlist | post → apply → shortlist E2E in chat; applicant's private details only visible to organizer |
| 3.2 | `marketplace` schema: price/negotiable, condition, delivery(pickup/shipping); reserve = book | offer + seek item E2E incl. reserve and confirm |
| 3.3 | `rides` schema: from → to, departure, seats, contribution | seat booking decrements capacity; full ride refuses |
| 3.4 | `gigs` schema: when-needed, duration, pay | one-message post + take-it booking E2E |

## Phase 4 — Chat UX

| # | Task | Done-when |
| --- | --- | --- |
| 4.1 | One-line posting with smart parsing + confirm step (`offer yoga event Sept 20 15:00 Rishikesh donation`) | parse → preview → one-word confirm → live post; ambiguous fields asked one at a time |
| 4.2 | `browse <vertical> [filters]`: by category, city, date range, tags — paged | `browse jobs remote` and `events in Berlin this weekend` return clean paged results |
| 4.3 | Post management in chat: `my-posts`, edit, pause/close, delete | owner-only enforcement verified |
| 4.4 | Help + honesty regression updated for every new command | chat-help test extended and green |

## Phase 5 — Notifications

| # | Task | Done-when |
| --- | --- | --- |
| 5.1 | Hub-side inbox per account (bounded ~100): booking/application/cancel/rating/expiry events | events land in inbox; bound evicts oldest |
| 5.2 | Auto-surface: any chat interaction shows unread summary + `inbox` command with read/unread | sleeping-organizer scenario: book while they're away → they log in → they see it |
| 5.3 | Mailbox push for self-hosted agents (send to registered Agentverse mailbox address; queue on failure) | push attempted when address known; offline queue verified |
| 5.4 | Two-way loop: organizer confirm/shortlist notifies the applicant's inbox the same way | full round trip E2E in chat |

## Phase 6 — Hardening + records

| # | Task | Done-when |
| --- | --- | --- |
| 6.1 | Report/spam flag: `report <post>` + operator review queue (minimal) | report lands in queue; only operator sees queue |
| 6.2 | Red-team pass over the new surface (unauth access, injection via tags/locations, inbox privacy) | new suite green, findings fixed |
| 6.3 | Full pipeline green in both trees; SPEC + PRIVACY + backlog-v3 updated; staging commit(s) | owner push ready |

## Execution order & size

Phases are strictly ordered (each builds on the last). Tasks inside a phase are small and independently verifiable. Estimated: P1 is the heavy lift (storage + registry refactor), P2–P5 are mostly additive, P6 closes it. Existing 16+ suites must stay green after every single task — the migration is behind the storage interface, so most tests shouldn't notice.
