# Community Vertical Schemas — contribution guide (C5)

EverList verticals are **DATA, not code**. The hub ships `events`, `food` and
`services` built in — and since C5 it also loads **community-contributed
vertical schemas** from `schemas/*.json` at boot. Want agents to book your
kind of thing (classes, tours, rentals, court time…)? You add a file, not a
pull request against hub logic.

## The contributed reference example: `classes`

`schemas/classes.json` (in this repo) adds a full `classes` vertical —
yoga, fitness, dance, cooking, art — with `instructor`, `skill_level` and
`duration_minutes` fields, capacity tracking, and `attendee`-based booking.
It is the copy-paste template for your own vertical.

## How to contribute

1. Copy `schemas/classes.json` to `schemas/<your-vertical>.json`.
2. Change `name` (3–16 chars, `[a-z][a-z0-9_]`), the field lists, the
   `categories` vocabulary and the `booking` block to fit your domain.
3. Validate locally: `HUB_SCHEMAS_DIR=yourdir python app.py 8803` — the boot
   log prints `[schemas] community vertical '<name>' loaded` or the exact
   rejection reason.
4. Open a PR with your file **plus a test** in the style of
   `test_c5_schemas.py` (create → book → search at minimum).

## Schema shape (all keys except `name` shown with their rules)

| Key | Rules |
| --- | --- |
| `name` | `[a-z][a-z0-9_]{2,15}`; must not collide with a built-in (built-ins **always win** — an override attempt is ignored, not an error) |
| `required` | non-empty; **must include `price`** (escrow accounting depends on it) |
| `optional` | no overlap with `required` |
| `categories` | non-empty, duplicate-free controlled vocabulary (validated at listing time) |
| `tracks_capacity` | `true` requires `capacity` in `required` (server counts registrations like events) |
| `field_types` | optional; values limited to the implemented validators `positive_int` and `nonempty`; keys must be declared fields |
| `booking.required` / `booking.fields` | `required ⊆ fields`; exactly one field is `booking.identity` (its real value goes to the secret store; the public copy gets an unlinkable anon-ref) |
| `booking.action` | display string like `register+pay` |

## The fail-closed guarantee (what the hub enforces on every file)

- Unknown top-level keys → rejected (no surprise semantics).
- Server-owned fields (`id`, `owner`, `registered`, `available`,
  `manage_code_hash`, `escrow`, `amount`, `hub_fee`, `owner_payout`,
  `booking_secret`, `rail`, `confirmation`, …) are **not community-settable**
  — a file asking for them is rejected.
- Reserved booking-field names are rejected (I1 field-ownership wall applies
  to community verticals automatically).
- A file that fails **any** check is rejected and logged
  (`[schemas] REJECTED <file>: <reason>`) — **the hub still boots and serves
  traffic**, and previously loaded schemas are unaffected. A bad community
  file can never crash the hub or weaken a built-in.
- All existing walls apply to community verticals with zero extra code:
  strict-int `quantity`, identity-field string walls, human-verified gate,
  rate limits, `_pub_listing` redaction.

## Runtime mechanics

- Loader runs once at boot (before `CLIENT_BOOKING_FIELDS` derivation), so
  community verticals extend booking, search, and chat automatically.
- Listing IDs use the vertical's 4-char prefix (`classes` → `clas-`);
  prefixes are derived, collisions would surface as shared counters (the
  name rules make this practically impossible; tests pin `clas-`).
- The chat agent reads the **live** `/verticals` registry: `vertical: classes`
  works in one message, unknown verticals get a hint that names the
  community verticals currently loaded.
