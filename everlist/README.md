# agent-hub-v2 — open agent commerce hub

Open-source (MIT), community-driven hub core proving the universal booking pattern:

discover → search → register & pay (escrow) → confirm/cancel → transparent ledger

## Fairness by design (enforced in protocol, not promises)

- **declared fee policy** — each hub declares its fee in the manifest; agents verify
  declared vs public ledger and choose (no protocol cap; transparency is the enforcement)
- **tiered registry** — open self-registration; `verified` requires operator review
  (authoritative registry service: `../registry/`)
- **open ledger** — every transaction + fee publicly queryable and independently
  cross-checkable (`../registry/check_hub.py`)
- **pseudonymous bookings** — public ledger shows pseudonyms only; real identity only
  via per-booking secret; we never store who you are
- **exact privacy docs** — what the hub stores, never stores, retention and deletion,
  code-mapped: see [PRIVACY.md](PRIVACY.md)
- **schemas are versioned + community-extensible** (verticals via schema files)

## Components

| File | Purpose |
| --- | --- |
| `app.py` | hub core: manifest, search, escrow bookings, ledger, x402 rails |
| `hublib.py` | versioned, domain-separated HMAC action tokens |
| `x402verify.py` | REAL EIP-3009 signed-payment verification (eth-account) |
| `x402facilitate.py` | idempotent settlement client with honest UNKNOWN semantics |
| `wrapper.py` | uAgents wrapper (Fetch/Agentverse ecosystem) |
| `sdk/agenthub/` | stdlib-only Python merchant/agent SDK (+ `examples/pizzeria.py`) |
| `SPEC.md` | normative protocol spec (endpoints, auth matrix, escrow state machine) |
| `/openapi.json` (served by the hub) | machine-readable API contract (OpenAPI 3.1) — agents read it natively; linked from the discovery manifest |
| `payments_x402.md` | x402 payment design + mode documentation |

## Payment modes

| Mode | Env | Meaning |
| --- | --- | --- |
| simulated | `HUB_PAY_MODE=simulated` | honestly labeled demo flow (default) |
| testnet | `HUB_PAY_MODE=testnet` | real signed EIP-3009 verification vs official USDC domains |
| settlement | `HUB_SETTLE_MODE=off\|auto` | off: verify only · auto: settle via facilitator (idempotent, `HUB_FACILITATOR_URL`) |

## Run

```bash
python3 app.py [port]        # default 8802
make test                    # full suite (unit + E2E + security)
```

See `SPEC.md` for the protocol contract and `../registry/` for the hub-of-hubs
registry + conformance checker.
