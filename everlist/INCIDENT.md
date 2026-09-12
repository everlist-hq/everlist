# Incident Playbook (S4)

What to do when something leaks or breaks. Checked against the real mechanisms in the hub (token `gen` counters, key restarts, backups) — no fantasy steps.

## 1. Operator admin key leaked

1. Generate a new key; restart the hub with `HUB_ADMIN_KEY=<new>` (fatal in production if dev-default).
2. Admin key grants token minting + vouch + sync — treat everything done while it was leaked as suspect: check `GET /admin/sync-escrow` history and account vouches.
3. Ledger check (C7) re-run: `make selfcheck`.

## 2. Booking signing key leaked

1. Restart hub with new `HUB_BOOKING_KEY`.
2. All outstanding tokens die (they verify against the key). Users re-login — account codes still valid (hashes unaffected).
3. Check bookings table for foreign entries (someone may have minted tokens while the key was out).

## 3. Wrapper agent seed leaked

1. Stop wrapper; delete `.secrets/agent_seed`; restart — new agent identity on Agentverse.
2. Update Agentverse registration if the agent address changes.
3. Old chats with the agent identity are orphaned (acceptable; the hub data is unaffected).

## 4. User account code leaked

User-initiated: `recover <email>` → new code minted, old code dead (gen counter bump). Operator cannot do this for the user — that is by design.

## 5. State file corrupted / ransomware-style

1. Stop hub (flock releases).
2. `tools/restore_backup.py --latest` (backups are 0600, pre-persist snapshots, newest-5 rotation).
3. Restart; run `make selfcheck` + full pipeline.

## 6. Midnight credential mass-revocation (issuer compromise)

Revocation propagates on next verify — accounts auto-downgrade (`midnight-zk-revoked`). No hub action needed; verify by running the M14 suite.

## 7. Database/state exfiltrated

Worst case is bounded: account code hashes (bcrypt-cost), booking secrets (plaintext — rotate by re-booking), no chain keys, no payout secrets. Notify affected users to re-book + rotate account codes via recovery. Payout keys are public — harmless alone.

## General order: stop → preserve → rotate → verify

Freeze state (`cp state.json incident-$(date +%s).json`) before any restore, so evidence survives the fix.
