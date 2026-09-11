# registry — authoritative hub-of-hubs service (D2/D3)

The real registry for the agent-hub protocol (app.py's built-in `/registry`
endpoint is DEMO-ONLY). Stdlib-only Python, zero dependencies.

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /hubs` | open + verified hub lists (self-registration can never claim `verified`) |
| `GET /registry.json` | **signed agent bootstrap document** (C6, see below) |
| `GET /registry.pub` | signing public key for out-of-band pinning |
| `POST /register` `{url}` | ownership proof: hub must echo a fresh nonce at `/challenge?nonce=...`, then its `/.well-known/agent-hub.json` is schema-validated |
| `GET /hubs/{id}` | record + hub's self-reported ledger totals (cross-checked by D3) |

## Signed registry document (C6)

`GET /registry.json` returns `{format: "everlist-registry/1-signed", payload, signature}`:

- **payload**: `{format, generated_unix, hub_count, hubs[]}` — each hub carries `url`, `tier`, `registered`, plus `protocol` / `api_contract` / `content_policy` carried from its registered manifest (schema versions, so agents can bootstrap from one well-known URL)
- **signature**: `ed25519` over the **canonical payload bytes** — `json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")` — so verifiers need no canonicalization logic beyond that one expression
- signing key: persistent, `0600`, gitignored (`registry-signing.key`, override `REGISTRY_SIGNING_KEY`)
- verify: `python3 check_hub.py --registry <registry_url> [--expect-hub <hub_url>] [--pubkey <pinned_hex>]` — bad/tampered signature or a wrong pinned key → `NON-CONFORMANT` (exit 1)
- trust note: the key is embedded in the envelope (agents can verify integrity + tamper-evidence immediately); production hardening = pin `/registry.pub` out-of-band and pass `--pubkey`

## Conformance checker

```bash
python3 check_hub.py <hub_url>
```

Verifies a hub's SELF-REPORTED consistency: declared fee vs charged fees,
arithmetic integrity (fee + payout = amount), escrow state validity, totals
vs recomputation. Verdicts: `CONFORMANT` / `NON-CONFORMANT` (exit 1) /
`INSUFFICIENT-EVIDENCE`. **Honesty note:** consistency is not a fairness
proof — independent settlement evidence does not exist at this stage.

## SSRF-safe fetch policy

https-only outside dev mode (`REGISTRY_DEV=1` allows loopback only), no
credentials in URLs, private/loopback/link-local destinations blocked,
redirects revalidated, bounded bytes/time. Disallowed targets are NEVER
contacted (fixture-proven).

## Run

```bash
python3 registry.py               # port 8810
python3 test_registry.py          # 9/9 tests
python3 test_check_hub.py         # 7/7 tests
```

State persists to `registry.json` (atomic writes, survives restarts).
