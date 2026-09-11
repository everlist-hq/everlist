# EverList Wallet UX — Midnight Tier-2 Verification Walkthrough

Status: **M15** · Date: 2026-09-11 · Contract: `credential.compact` (M13) · Hub wiring: `/accounts/verify-midnight` (M14)

> **The two-tier law (permanent):** Tier-1 easy signup exists **forever** and never gets harder.
> Tier-2 (Midnight) is a pure **benefit layer**: verified-human badge and stronger trust.
> Midnight rewards you; it never blocks you.

---

## 1. What Tier-2 actually is

Your EverList account gets `verified_by: midnight-zk` — set **by the hub, server-side, only after it
reads the credential contract's public state** and finds your credential **admitted + not revoked**.

You never hand the hub a secret. The hub never sees one. That is the whole point.

---

## 2. First-time registration (pilot issuer flow)

> **Honest status:** no public Midnight personhood issuer exists yet. **EverList's operator is the
> pilot issuer** — the issuance step below is run by the operator today. When a real personhood
> issuer ships on Midnight, this step is replaced; nothing else in your flow changes.

### Step 1 — Your holder secret (you, in your wallet)

Generate a 32-byte secret. This is the **only** secret Tier-2 ever involves:

```
openssl rand -hex 32        # -> <holder-sk>  (keep in your wallet; NEVER send to anyone)
```

### Step 2 — Your holder commitment (you, in your wallet)

The commitment is what enters the contract — derived by the **holderCommitment circuit**, never by hand:

```js
// tools/compute-commitment.mjs  (in experiments/midnight-escrow/contract)
import { readFileSync, writeFileSync } from 'node:fs';
const { pureCircuits } = await import('./managed-credential/contract/index.js');
const sk = Uint8Array.from(readFileSync(process.argv[2]).toString().trim().match(/.{2}/g)
    .map(b => parseInt(b, 16)));
console.log(Buffer.from(pureCircuits.holderCommitment(sk)).toString('hex'));
```

```bash
node tools/compute-commitment.mjs ./my-holder-sk.txt   # -> 64-hex <holder-comm>
```

> Midnight contract tooling (credential + escrow circuits, scenarios, this helper) ships with
> the Midnight integration workspace and is **not part of the hub repo yet** — it joins the
> published repo when the Midnight track graduates (planned).

(The **wallet always computes its own commitment** from its own secret — see
`credential-scenario.mjs` line 82 for the exact pattern. You only ever reveal the public
commitment; it's stored in the contract's public state anyway.)

### Step 3 — Issuance (operator, pilot phase)

The operator calls the contract's `issueCredential(<holder-comm>)` circuit. The contract:

- stores `holders[nextId] = <holder-comm>` in its **public ledger state**
- marks `revoked[nextId] = false`
- returns your **credential id** (a small integer, e.g. `3`)

### Step 4 — Verify with the hub (you, in chat or API)

```
login-seed <your seed>            # Tier-1 login (unchanged)
verify-midnight 3                 # your credential id
```

Chat answer:

```
✅ Midnight credential 3 verified (simulated mode, tx …ffffffff)
You are now human-verified via Tier-2 — bookings need no extra credential.
```

Optional stronger binding: send the **public** `holder_commitment` (64-hex) with the request —
the hub then rejects the id if the on-chain commitment doesn't match what you present.

**Done.** `whoami` now shows:

```
Logged in as acct-… · ✅ verified: Midnight ZK credential (Tier-2) · listing cap 25 · …
```

---

## 3. Daily flow (already verified)

Nothing new to remember:

| You do | You get |
| --- | --- |
| `login-seed <seed>` (or session persists) | normal account access |
| `whoami` | shows the Tier-2 badge + provenance |
| `list` / `book` / `rate` | exactly as before — verification rides along automatically |

Bookings you create carry `verified_by: midnight-zk` **server-side** (organizers and public
listing views see the provenance; clients can never fake it — the field is reserved).

Tier-1 accounts work exactly as always; the only difference is the badge and trust.

---

## 4. Who sees what (secrets table)

| Item | Lives where | Ever sent to hub? |
| --- | --- | --- |
| Holder secret (`<holder-sk>`) | **your wallet only** | ❌ never |
| Holder commitment (`<holder-comm>`) | public contract state | optional, and it's **public by design** |
| Credential id (`3`) | contract + your notes | ✅ yes (it's just an index) |
| Hub's issuer secret | operator `.secrets/` (0600, gitignored) | ❌ never leaves operator |
| Your EverList seed | your device, shown once at signup | ❌ never (challenge-response) |

The hub's verification is a **read** of public chain state. It cannot leak what isn't shared.

---

## 5. Revocation & recovery

- The issuer can `revokeCredential(<id>)` on-chain. The next time the hub re-checks your
  credential, your account is **downgraded**: badge lost, `verified_by: midnight-zk-revoked`,
  and chat tells you plainly (`❌ credential … is REVOKED on-chain`).
- Verification is **not a one-time claim** — the hub can re-check at any verify call; a revoked
  credential can never re-verify while the revocation stands.
- Lost your credential id? The operator can look it up by your commitment. Lost your holder
  secret? The credential still verifies (state check) — treat the secret as the thing that
  *proves* you in future ZK flows, and guard it accordingly.

---

## 6. Mode honesty (every answer is labeled)

| Mode | When | Meaning |
| --- | --- | --- |
| `simulated` | default (no `HUB_CRED_INDEXER`) | evidence = recorded timeline of the **real compiled circuits** (offline simulator) |
| `chain` | `HUB_CRED_INDEXER` set | evidence = live Midnight indexer reads of the deployed contract |

Any verifier failure (unreachable, unparsable, unknown credential) → **fail-closed**: HTTP 502 or
403, `verified_by: None`, nothing mutated. The hub never guesses you human.

---

## 7. What is NOT built yet (honest gaps)

| Gap | Status | Path |
| --- | --- | --- |
| Live-chain deployment of `credential.compact` | preprod verified reachable (M-C); faucet/deploy pending S-A1-3 spike | M11 follow-up |
| Public personhood issuer | none exists — operator is pilot issuer | replace Step 3 when available |
| Full ZK proof-presentation | today the hub reads **public state** + optional commitment binding; the `proveHolder` circuit + proof-server path is the upgrade | post-pilot hardening |

---

## 8. Operator quick-reference (pilot issuance)

```bash
# full issue→verify→revoke→verify-fails cycle against the compiled circuits (6 checks):
npx tsx --test test/credential.test.ts                 # in experiments/midnight-escrow
node credential-scenario.mjs                           # live scenario: prints ids + commitments

# hub-side env (simulated mode, default):
#   HUB_CRED_ADDRESS  = contract address (default 'midnight-credential-sim')
#   HUB_CRED_FIXTURE  = timeline JSON override (tests use this)
# chain mode: HUB_CRED_INDEXER=https://indexer.preprod.midnight.network/api/v4/graphql
```
