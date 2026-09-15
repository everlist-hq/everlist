
## 2026-09-10 — B1: token revocation (night-shift directive: work the backlog)
- Implemented per-account `gen` counter (state.json, migrated default 0). Login tokens embed `gen` at mint (hublib.mint_token `extra`). New `_gen_check()` guard applied at all 8 account-token sites (listings GET/POST, manage, bookings, book, archived view, email-bind). Bumped on: /accounts/rotate, email/recover/confirm (legacy + keypair branches), new POST /accounts/logout-all. Chat: `logout-all` command + help line. SPEC §12/§12a updated.
- **REAL BUGS FOUND by live chain (both fixed + regression-tested):** (1) /accounts/rotate and /accounts/email/bind iterated ACCOUNTS assuming `code_hash` on every kind — KeyError crash (RemoteDisconnected) whenever a keypair account existed in the same store; guarded both scans with `v.get("code_hash")`. (2) Chat signup note: hub signup returns code only (no tokens); login is a separate call — chat auto-login path unaffected.
- Verification: live chain 5/5 (rotate kills old token 401; logout-all kills all; new code logs in; same account id; anon /access token unaffected). Hardening suite: 10 new B1 checks incl. mixed-kind regression — 52/52. make test: G2 + hardening + E2E escrow HELD — ALL PASSED. Staging commit 78f262e. Live stack restored + verified.

## 2026-09-10 — B2: concurrency proof battery
- New test_concurrency.py (self-managed fresh hub, HUB_POW_SIGNUP_BITS=8, HUB_TRUST_PROXY=1): 40 parallel signups from 40 distinct simulated sources (per-source fairness exercised via X-Forwarded-For) -> zero 500s, all 201, state.json judged: exactly 40 accounts, zero duplicate ids/code-hashes/pubkeys. 30 parallel /listings by one account -> all 201, zero duplicate ids, monotonic id counters exact in state.json. 13/13 checks, ~1.1s per phase.
- Hub addition (backlog-sanctioned): PoW difficulty now env-tunable (HUB_POW_SIGNUP_BITS / HUB_POW_RECOVER_BITS, defaults 18/16 unchanged). Wired battery into make test AND CI (.github/workflows/tests.yml).
- Backlog deviation recorded honestly: hub deliberately has NO per-account listing cap (cap-25 is chatlib's anti-spam layer); battery proves what the hub guarantees — uniqueness + atomicity + zero 500s under parallel fire.
- make test ALL PASSED (G2 + hardening 52/52 + concurrency 13/13 + E2E escrow HELD). Staging commit recorded. Live stack restored.

## 2026-09-10 — B3: state profiling spike (read-only) + B4 decision data
- New tools/state_profile.py: loads real state.json as template, synthesizes worst-case scale, times the EXACT production persist path (json.dump -> tmp -> os.replace, no fsync) + startup load. Read-only: synthetic writes go to a temp dir only.
- Measured decision curve (this container, default iters):
  * pilot-realistic 1k accounts / 500 listings (0.7 MB): persist avg 29.2ms, load ~10ms -> JSON OK, B4 optional
  * backlog target 10k accounts / 5k listings (7.3 MB): persist avg 288ms (max 305ms), load 96ms -> OVER the 200ms gate
  * headroom 25k accounts / 12k listings (18 MB): persist avg 707ms -> far over
- DATA-DRIVEN DECISION: B4 (SQLite adapter) NOT mandatory now — the backlog gate says "if >200ms at realistic scale" and realistic pilot scale measures 29ms. DEFERRED with explicit triggers: implement B4 when accounts approach ~3-5k, listings ~2k+, or persist p95 exceeds ~100ms in production monitoring; also reconsider before any public growth push.
- Bonus finding for B4 regardless of gate: the persist path has NO fsync — durability on power-cut is best-effort (atomicity vs a crashed process is preserved by os.replace, but an OS crash can lose the last writes). SQLite WAL mode fixes both.
- Verdict printed by the tool itself; re-run anytime: python tools/state_profile.py [--accounts N --listings N].

## 2026-09-10 — B5: SDK Tier-1 keypair support (unfrozen: Tier-1 signup is permanent)
- sdk/agenthub/client.py: generate_keypair() (os.urandom 64-hex), signup_keypair(agent, seed=None) (local PoW solve, hub stores ONLY pubkey, auto-login), login_seed(seed, agent) (challenge-response over b'everlist-login:'+challenge, byte-identical to chatlib wire format). Crypto imports LAZY: rest of SDK stays stdlib-only.
- sdk/examples/pizzeria.py: TRY_KEYPAIR=1 env guard demos the Tier-1 flow; default flow unchanged.
- test_sdk_accounts.py (self-managed hub, PoW bits 8): 10/10 — keypair generation, signup with pre-made seed, SEED NEVER PERSISTED AT HUB (state.json raw scan), pubkey stored, fresh-instance login_seed, add_listing owned by account (server-side owner), orders() reachable, unknown seed -> real HubError 404 (no crash).
- Test bugs fixed en route: seed must be passed explicitly to signup_keypair (it generates its own otherwise); events vertical requires date.
- make test ALL PASSED (G2 + hardening 52/52 + concurrency 13/13 + sdk-accounts 10/10 + E2E escrow HELD). Wired into make test + CI. Staging commit. Live stack restored after.

## 2026-09-10 — B6: chat E2E via real wrapper — THREE P0 PRODUCTION BUGS FOUND & FIXED
- Built test_chat_e2e.py: full conversation through the REAL wrapper uAgent (not chatlib in isolation): sender A signup (keypair seed-once, auto-login) -> one-prompt list -> sender B login-seed from a DIFFERENT address -> codeless edit. Asserts replies + state.json ownership. In-process session tracing (the wrapper's own chatlib._SESSIONS) was the decisive microscope.
- BUG 1 (P0, live impact): wrapper.py used asyncio.to_thread and imported chatlib WITHOUT importing either module — every chat message on the live Agentverse agent failed with NameError behind the safe error envelope. Nobody could chat with EverList until fixed. Found because B6 ran the real wrapper; chatlib-only tests never touched it.
- BUG 2 (P0): hub rejected real uAgent addresses at 65 chars (agent limit was >64) on /access, /accounts/signup, /accounts/login — every real Agentverse agent login was rejected; all earlier tests passed because they used short friendly names. Fixed to 128 + regression check (65-char agent signup must return 201).
- BUG 3 (P0, subtle): chatlib._create_listing clamped sender[:64] (stale pre-crypto-era code) — signup stored the session under the full 65-char address, list looked it up under 64 chars -> session MISS -> listing silently created anonymous (manage-code mode) despite logged-in account. Verified via call-level trace: set_session(65-char) then _session(64-char) 5ms later. Fixed to [:128] at both sites (502 too).
- BONUS: B2 concurrency flake exposed ThreadingHTTPServer default listen backlog of 5 refusing burst connections (40 parallel signups -> -1 transports). Raised request_queue_size to 128 — bursty Agentverse/SDK traffic now queues instead of being refused.
- make test ALL PASSED: G2 + hardening 52/52 + concurrency 13/13 + sdk-accounts 10/10 + chat-e2e 8/8 + A2A E2E escrow HELD. Wired into Makefile + CI. Staging commit. Live stack restarted with all fixes.
- Method note: the in-process trace (monkeypatching the wrapper's own chatlib module from the test) localized all three bugs in minutes; theories alone were wrong twice. Instrument first, theorize less.

## 2026-09-10 — B7: public-read sanity limits
- app.py: READ_Q_MAX=200 (search q cap, 400 with reason), _paginate() offset/limit (default 500, clamp 1..HUB_READ_PAGE_MAX; count stays FULL — backward compatible response, new fields offset/limit/returned), _read_gate() per-source backstop HUB_READ_LIMIT=600/min on /search + /listings (429 honest message). Generous by design: legit agents never feel it.
- chatlib._smart_search: hub HTTP rejections now surface the REAL reason ('Search rejected: q too long...') instead of the misleading 'unreachable' (same honesty lesson as B4 signup fix).
- test_read_limits.py 15/15 (two self-managed hubs): silence at 100 rapid reads, exact 200/201 boundary, pagination math + clamps + invalid fallbacks, chat honesty, tuned hub (HUB_READ_LIMIT=50) trips EXACTLY at read 51 and keeps blocking within window.
- Own bug caught by own test: passed u.query (string) into _paginate (needed parse_qs dict) -> handler crashed, hub closed connection without response. Test surfaced it as RemoteDisconnected; fixed + hardened test req() to return -1 instead of crashing.
- make test ALL PASSED (now 6 stages). Wired Makefile + CI. SPEC §15 added. Live stack restarted — read gates active on :8802.

## 2026-09-10 — B8: state backup rotation
- app.py: _backup_locked() runs at the START of every _persist_locked — copies the CURRENT (pre-persist) state.json to <state_dir>/backups/state-<ns>.json when it exceeds HUB_BACKUP_MIN_BYTES (default 1MB), keeps newest HUB_BACKUP_KEEP (5), chronological (time_ns names sort correctly). Backup failure = logged warning, persistence NEVER breaks (try/except around everything).
- test_backups.py 5/5 (forced tiny threshold): exactly 5 backups after 8 persists, chronological ordering, valid JSON snapshots, cap holds after 9th persist, newest backup hash == pre-persist state hash (byte-exact proof of restore point).
- make test ALL PASSED (now 7 stages incl. B8). Wired Makefile + CI. SPEC §16 added. Live stack restarted with rotation active.

## 2026-09-10 — B9: registry checker verifies the auth contract (C6)
- app.py manifest: new "auth" block — kind: crypto-accounts, contract: SPEC 12a, challenge: /auth/challenge, signup: /accounts/signup. A hub now DECLARES its account surface.
- check_hub.py C6: only hubs ADVERTISING accounts must serve the contract — GET <auth.challenge>?kind=signup must return well-formed challenge (algo, challenge, difficulty:int, ttl:int). Advertised-but-missing/malformed -> NON-CONFORMANT. No auth block -> 'C6 skipped (auth optional)' — never a flag for hubs without accounts.
- test_check_hub.py: make_stub(manifest=, extra_get=) parametrization; D7 valid challenge CONFORMANT, D8 challenge-404 NON-CONFORMANT, D9 malformed NON-CONFORMANT. Suite 10/10.
- Live proof: real hub (fresh stack) -> C6 ok: crypto challenge served (algo=sha256-leading-zeros, difficulty=18, ttl=600s), VERDICT CONFORMANT.

## 2026-09-10 — B10: chat help honesty (FINAL backlog item) — 2 more chat bugs found & fixed
- chatlib: help text extracted to single _HELP constant — now COMPLETE (17 commands: search/list both formats, signup, login-seed, login, email-bind/code, recover/recover-confirm, whoami/logout/logout-all, my-listings, edit, delete, archive/unarchive, book, fee; signup honestly described as keypair with seed-once).
- test_chat_help.py (self-managed hub): help coverage + discovery of true replies + universal gate (no documented command falls through to search-fallback) + per-intent signatures + positive whoami-after-signup. 10/10.
- BUG A (caught by discovery run): chat `login <bogus-code>` answered 'Welcome back! ... account None' — _hub_post RETURNS (status, body) on rejections but _login discarded the status and minted a session from the error body. Rejected logins now never create sessions.
- BUG B (cascade): manage commands (edit/delete/archive/unarchive) with a poisoned/expired session said 'hub unreachable' (KeyError swallowed). Now: missing token -> honest 'Session expired' guidance; real hub rejections surface their reason.
- Method note: the unreachable-hub test design was wrong (real handlers and fallback share the 'unreachable' reply) — switched to self-managed-hub discovery-first design; printed actual replies first, then locked signatures. Discovery caught both bugs immediately.
- make test ALL PASSED (8 stages). Wired Makefile + CI. Live stack restarted with the login fix.

## 2026-09-10 — Backlog v2 Run 1
### H1: fresh-clone smoke test — PASSED CLEAN (no product changes needed)
- Method: git archive HEAD from .staging-repo -> /tmp/h1-clone (byte-identical to what GitHub serves; verified no venv/.run/.secrets in the tree), fresh python3 venv, pip install -r requirements.txt (cold), make up on :8810.
- Results: install clean (all deps resolve incl. uagents 0.25.5, cryptography 50.0.1); imports OK; manifest served; G2 suite 57/57 from the stranger tree; chatlib one-prompt listing created (even-1) + search found it. make down + cleanup.
- Verdict: the public repo will work from zero. requirements.txt is complete. F2 push is technically unblocked.
### H2: backup restore drill — DONE (tool + permanent test, pipeline wired)
- tools/restore_backup.py: --list/--latest/--file N, --check dry-run, refuses while state is flocked, pre-restore undo snapshot, atomic replace, snapshot validation (listings/bookings/ledger keys).
- test_restore_drill.py (10 checks): 6 persists -> exactly 5 backups (pre-persist semantics discovered from code, first persist has no prior file); --list/--check; refuse-while-flocked + proceed-after-release; SIGTERM hub -> restore backup[1] -> state byte-identical to chosen backup (sha256) -> hub restart serves restored listings + consistent ledger.
- 2 initial test-assertion failures were MY wrong assumptions, not product bugs (backup count semantics; hub does not yet hold the state lock until H4 lands — guard proven via simulated lock-holder).
- Wired into Makefile test pipeline (after B8) + CI workflow; full make test ALL PASSED (10 stages). Staging synced (tool, drill, Makefile, CI).

### H3: fsync durability — DONE (default ON, env kill-switch)
- app.py _persist_locked: fsync temp file before os.replace + fsync containing directory after (rename itself needs the dir fsync on POSIX). HUB_FSYNC=1 default ON, 0 disables.
- tools/state_profile.py: added --fsync flag to profile the real production path; honest notes updated (no-fsync default kept for B3-baseline comparability).
- Timings (worklog record per done-when): pilot scale 1k accounts/200 listings/548KB payload, 30 persists: fsync OFF avg 19.2ms p95 20.7ms; fsync ON avg 33.0ms p95 35.1ms -> cost ~13.8ms/persist. Profiler at 500/100: 11.2ms no-fsync vs 26.0ms fsync. Both far under B4 gate (p95>100ms) -> durability is cheap, stays default ON.
- Full make test ALL PASSED with fsync ON (10 stages incl. H2 drill + A2A E2E escrow HELD).

### H4: single-instance guard — DONE (exit 79, crash-safe)
- app.py __main__: non-blocking flock on <state>.lock held for process lifetime (fd stays open); second hub on same state -> clear REFUSING error + exit 79, never listens. Makefile port guard cannot catch same-state-different-port starts; this closes split-brain corruption.
- test_single_instance.py (7 checks): first hub serves; same-state second hub exits 79 with explanatory message + never listens; first hub unaffected; different-state parallel hubs legit; SIGKILL crash -> stale lock auto-recovers on restart.
- REAL FINDING during integration: H4's hub-held lock broke H2's --check (dry-run refused while hub live). Fixed restore_backup.py ordering: verify/stats/dry-run BEFORE the live-lock refusal — dry-run is read-only, operators may preview while live. Drill updated to prove the REAL hub-held lock (supersedes simulated holder). restore-drill 10/10 again.
- Full make test ALL PASSED (11 stages). Wired into Makefile + CI.

### H5: limiter coverage audit — DONE (5 new kinds, 6 routes covered)
- Audit: 13 POST routes; 6 were uncovered. Added per-source backstops (AUTH_LIMITS): access 60/min, create 60/min, book 60/min (plus G4 per-principal), manage 60/min, admin 30/min shared by /accounts/vouch + /admin/tokens. Env knobs HUB_LIMIT_*; defaults sized 2-3x above measured legit volume (pipeline peaks ~25 writes/min; B2 races 30 creates) so only abusers feel them.
- Brute-force property proven: manage wrong-code guesses and admin wrong-key guesses COUNT toward the limit (403s then 429) — brute force dies at the limit, not at the secret.
- test_write_limits.py (10 checks): per-kind floods 429 at limit+1, first-N allowed, fairness (flooded source limited, fresh sources unaffected).
- SPEC 15: full limiter table documented; logout-all documented as intentionally unlimited (success self-revokes).
- Wiring: Makefile + CI stage; full make test ALL PASSED (12 stages).
- Note: root strays (state.json.lock, state.json.g4.lock, wrapper.log) reappear from a test spawning a hub with default state path — origin in test_x402.py; cosmetic (untracked), revisit in H6/cleanup.

### H6: dependency hygiene — DONE (Run 1 complete)
- requirements.txt: cryptography pinned to exact 50.0.1 (H1 fresh-clone-proven); all direct deps now exact-pinned.
- pip-audit run: 3 findings in 2 TRANSITIVE packages, both researched and waived with reasoning in SPEC 17: pynacl 1.6.0 PYSEC-2026-3002 (libsodium edge API unreachable via our stack; cosmpy pins ==1.6.0 so fix 1.6.2 breaks the tree) and ecdsa 0.19.2 PYSEC-2026-1325 (P-256 signing timing attack, no fixed version exists; our crypto is Ed25519 via cryptography, ecdsa only in rare local agent registration). Waived audit exits 0.
- make audit target added (checks pip-audit present, runs with the two waivers); CI step added (non-blocking, continue-on-error until tree clean upstream).
- Hygiene extras: .gitignore now covers *.lock (H4 lockfiles); pynacl briefly upgraded then reverted to 1.6.0 for tree consistency (pip check clean).
- Session lesson: my H5 probe hub on :8811 was left orphaned (kill %1 hit a different shell) and its default-state path recreated root strays — killed, cleaned; live stack lockfile correctly lives in .run/ now.
- Full make test ALL PASSED (12 stages) + audit green.

## RUN 1 COMPLETE: H1 fresh-clone, H2 restore drill, H3 fsync, H4 single-instance, H5 limiter coverage, H6 dependency hygiene — the F2 push quality gate is satisfied.

## 2026-09-10 Run 2 (session 2)
- H7 DONE: GDPR account deletion. Hub: do_DELETE /accounts/me (token-authed, typed confirm, per-source delete limiter). Owned listings archived not destroyed; bookings+ledger untouched (money trail survives); account+recovery email erased; tokens die via _gen_check. Chat: two-step delete-account with typed account-id confirm, session wipe, help updated. Test: 26 checks (auth wall, confirm gate, archival proof incl. 409-on-booking, ledger byte-identical, bystander untouched, limiter flood, chat flow). Staging 24f93e4. Lesson re-learned: discovery-first — my test guessed wrong contract (POST /book {attendee}, not /bookings {name}); hub correct all along.
- H8 DONE: PRIVACY.md exact privacy doc (stores: pubkey/hash-only codes, plaintext recovery email why+deletion; never: seeds, passwords, disk-side rate/session data, PII in ledger; backup-generation limitation stated). README + SPEC §18. Staged commit.
- H9 DONE: GET /listings/{id} + REAL VULN FIX: manage_code_hash (sha256 of 48-bit mgr- code) leaked in /listings, /search, premium {**l} echoes — offline-brute-forceable. _pub_listing sanitizer at all 5 echo sites; codes bumped to 64-bit; single fetch 404/410; SDK get_listing; chat show. 16/16 in pipeline. Lesson: discovery-first probe of LIVE state.json keys exposed the leak instantly.
- H10 DONE: booking status lookup. GET /bookings/{id} buyer/owner-scoped, 404 no-oracle; SDK get_booking (HubError); chat booking <id> with deterministic anonymous re-mint + account session paths. 15/15. Key insight: /access principal = agent name (deterministic) so anonymous agents can poll their own bookings forever.
- H11 DONE: SDK robustness. Typed HubNetworkError; central timeout HUB_SDK_TIMEOUT; GET-only single retry (connect errors + 502/503/504); POSTs never retried — flaky-stub test proves exactly-one POST under 503 (no double-booking). Caught real bug: super().__init__ overwrote self.status=None with 0 (order fixed). 10/10; pipeline 15 stages ALL PASSED.
- H12 DONE: hub request logging. Structured lines + X-Request-Id; query-free paths only; real rotation bounded; fail-open on bad log path. 10/10; pipeline 16 stages ALL PASSED. Design note: default log beside STATE_FILE so test hubs never contend on one file.
- H13 DONE: OpenAPI 3.1 contract at /openapi.json. 31 ops; manifest points to it (api_contract); SPEC+README reference it. Cant-lie test: junk-route 404 catch-alls as control, then every documented op must differ — spec cannot lie about a route. 12/12; pipeline 17 stages ALL PASSED. Adoption lever: agents read openapi natively.
- H14 DONE: make selfcheck (on-demand, no scheduler). tools/selfcheck.py read-only probe, works against any hub URL. 7/7; pipeline 18 stages ALL PASSED. Caught my own heredoc eating the Makefile recipe tab (missing-separator trap) — fixed via text_editor, rule holds: never heredoc Makefile recipes.
- H15 DONE: services vertical, the extensibility proof. Vertical = data (schema entry); hub de-hardcoded 6 sites; chat + SDK extended; WAIVED lifecycle completed (confirm/cancel). 20/20 + full pipeline green with zero edits to existing suites. Lesson re-learned: heredoc mangles backslash-n escapes in Python source (broke chatlib.py mid-patch, recovered from staging commit f36d523) — escape-heavy patches go through text_editor or raw-string patch scripts, never heredocs.
- H16 DONE: demo seed + reset, one-command reproducible demo verified live. Finds: /access 201 vs 200; built-in module-level seed data on fresh boots (empty-snapshot replace, zero app.py changes). RUN 2 COMPLETE (H7-H16, 10/10).
- Run 3 DONE (H17-H18): red-team pass 2 over the new surface, discovery-first. 5 real fixes (file perms 0600/0700 on secret state, schema-driven identity bounds, strict-bool human proof, schema-derived edit allowlist, bounded required strings) + error-text hygiene. 37/37 suite. Test-contract lessons: token-OR-code auth semantics; capacity>=registered equality is legal.
- Run 3 close-out: staging pipeline ALL PASSED from the staged tree (push gate green); E2E de-hardcoded (search-derived target, amount-aware escrow); dependency preflight added; venv reprovisioned; staging commit 7; live stack restarted.

## 2026-09-11 — Midnight Phase A: A1 escrow state machine VERIFIED OFFLINE (12/12)
- Owner GO received (P1 postponed until Midnight escrow integrated). Experiment: experiments/midnight-escrow/
- Env verdict: NO docker/podman, userns+mount-ns blocked → official proof-server/local-devnet path impossible in-container; native path chosen
- Toolchain: compact CLI v0.5.2 + compiler 0.34.0 installed NATIVELY (release checksums verified; missing `unzip` discovered via binary strings — installer left partial artifact that had to be cleaned before retry)
- Contract contract/src/escrow.compact (Compact 0.26): 4 circuits — createEscrow (agent commitment from witness, organizer commitment public input, state HELD), releaseEscrow (organizer-only, HELD→RELEASED), refundEscrow (agent-only, HELD→REFUNDED), isHeld (public read). Parties = persistentHash commitments, never addresses. Compiler-enforced lessons: no top-level consts; disclosure discipline (disclose() at ledger writes; witness→hash→assert needs none — followed official leaderboard pattern)
- COMPILED: 4 circuits → ZKIR + prover/verifier keys + TS bindings (managed/)
- Offline harness test/escrow.test.ts runs REAL compiled circuits via compact-runtime 0.19 createCircuitContext + ContractState. Runtime semantics discovered the hard way: (1) contract constructor (initialState) MUST run to initialize Counter/Map cells; (2) ledger() wants state.data (ChargedState), not ContractState; (3) circuit calls do NOT mutate state in place — each returns a NEW context that must be threaded; (4) role walls need party-accurate instances (organizer machine holds ONLY its own secret)
- RESULT: 12/12 tests PASS — full state machine + attacker walls + role walls + double-transition rejections + monotonic ids, on real compiled circuits, zero docker
- Honest gate remaining: ZK proof generation needs the proof server (docker) — A1 proves the circuit LOGIC; real proofs/deploy = next increment (needs docker-enabled host or extracted binary), documented

## 2026-09-11 — Backlog v3 created
- docs/backlog-v3.md: 7 tracks (M Midnight / C core / O ops / P pilot / S security / X docs / Q quality), ~45 items, each with done-when
- v2 marked SUPERSEDED (16/23 done; gated leftovers H19/H21/H22/H23 folded into Track C)
- Standing directives embedded (established paths, two-tier identity, no schedulers, never push, P1 gated until Midnight integrated, discovery-first, no node -e probes)
- Suggested order: M1+M2 proof path → M3+M4 funded/timeout circuits → M5–M8 mirror E2E → M9–M12 keys/fees → C1–C3 → M13–M15 personhood → O track → S before pilot

## 2026-09-11 — M1 DONE: native proof-server + REAL ZK proof (no docker)
- Extracted official proof-server binaries from Docker image layers via Registry API (curl+tar; verified digests): 7.0.0-rc.1, 8.1.0, 9.0.0-rc.7 — run natively via extracted nix glibc linker + matching xgcc-libgcc on LD_LIBRARY_PATH (launcher: tools/proofsrv/run_proofserver.sh)
- Version lattice decoded empirically: compact 0.34.0 / compact-runtime 0.19.0 / ocrt-v4 ↔ ledger-v9 (1.0.0-rc.4) ↔ proof-server 9.0.0-rc.7; the 8.1.0 server line pairs with the OLD compact-runtime 0.16.0 generation and rejected the new dialect
- Three wire fixes: ledger-v9 envelope (not v8) · preimage serialized by ledger-v9's own proofDataIntoSerializedPreimage (tagged format) · IR = tagged binary .bzkir (midnight:ir-source[v2]: header), not JSON .zkir
- Real ProofData captured from ctx.callProofDataTrace after a genuine createEscrow call (generated code builds it via finalizeCallProofData)
- RESULT: POST /check 200 (parsed costs) + POST /prove 200 → 4508-byte proof starting midnight:proof-v… in ~2s — contract/probe-v9.mjs, server live on :6301
- Backlog M1 marked DONE with full recipe; README rewritten (gate solved); stale 8.1.0 server stopped, 9.0.0-rc.7 kept
- Next in track: M2 (zk-params provisioning — first prove already fetched keys fine), M3 funded Zswap escrow

## 2026-09-11 (later) — Midnight track: M2 + M4 complete

- **M2 ✅** — all three transaction circuits (createEscrow, releaseEscrow, refundEscrow) proven natively on proof-server 9.0.0-rc.7 (4508-byte proofs, ~2s each, `probe-m2.mjs`). Discoveries: `isHeld` is a ledger QUERY — returns results but emits NO proof-data trace (reads are node-evaluated, not transactional); trace entries carry the CONTEXT-creation circuitId, not the called circuit's (probe overrides with the true name). Honest transfer of the isHeld-based done-when documented in backlog.
- **M4 ✅** — `timeoutRefund` permissionless liveness: `deadline: Uint<64>` on the Escrow struct + createEscrow param (release/refund preserve it); after deadline ANYONE may trigger the refund. Verified offline 17/17 (before-deadline rejects · after-deadline refunds via the ATTACKER instance · post-release/post-refund reject · deadline stored) + bonus: timeoutRefund proven natively (2940 bytes, `probe-m4.mjs`) — all four transaction circuits now have real ZK proofs. Insight: offline context block-time defaults to now (~1.78e9), so deadline=1000n (1970) is always 'past' and 99999999999n always 'future' — no custom-time contexts needed for testing.
- **M3 recon done** — exact coin-flow syntax acquired from authoritative sources (docs site 429s; used raw GitHub of midnight-docs stdlib reference + midnightntwrk/passport control.compact as the production pattern, GITHUB_TOKEN auth): `receiveShielded(coin: ShieldedCoinInfo)` · `sendShielded(input: QualifiedShieldedCoinInfo, recipient: Either<ZswapCoinPublicKey, ContractAddress>, value: Uint<128>) → ShieldedSendResult` with change management via `result.change.is_some` + `insertCoin(..., right<ZswapCoinPublicKey, ContractAddress>(kernel.self()))` · `mergeCoinImmediate` for multi-deposit · block-time fns (`blockTimeGte`) confirmed in the 0.26 stdlib by compile.
- Next in track: **M3 funded escrow** — deposit/release/refund coin flows in the simulated zswap state.

## 2026-09-11 (later still) — Midnight track: M3 complete (funded escrow)

- **M3 ✅** — escrows are FUNDED: `createEscrow` deposits an exact-value shielded coin into contract custody (`receiveShielded`, value==amount asserted); release/refund/timeout spend it via `sendShielded` to payout keys stored at creation (ledger maps `agentPks`/`organizerPks`) — destinations fixed at create, so even permissionless timeout cannot redirect funds. Custody (nonce/color) on the Escrow struct; spenders supply a QSCI asserted against custody. Verified offline **23/23** + funded create/release proven natively (4508 bytes, 4.4s/7.8s).
- Type facts (probe-m3-coins.mjs): circuit-level pk args = `{bytes: Uint8Array(32)}` (plain string only for the context ctor); QSCI field is **`mt_index`** snake_case; deposit's merkle index = `comIndices.size` captured before the create call; deposit output recipient `is_left=false` (contract address), payouts `is_left=true` (user pk).
- Compiler caught an undeclared witness disclosure (pk → ledger map insert) — fixed with explicit `disclose()` (payout destinations are public by design).
- Run M-A (M1–M4) is COMPLETE. Next: Run M-B — chain↔hub mirror (M5–M8).


## 2026-09-11 ~14:40 — Run M-B begins: M5 escrow_ref DONE
- Hub (experiments/agent-hub-v2/app.py): optional client-supplied `escrow_ref` {contract, escrow_id, tx} on /book — exact-keys wall, bounded strings (80/100), positive-int id, WAIVED-contradiction 409 (free listings have no escrow rail), stored verbatim after validation, exposed in /bookings + /orders + 201 response, OpenAPI note updated.
- Test: test_m5_escrow_ref.py 22/22 (accept/echo, 13 shape walls, contradiction, backward-compat, idempotency replay, OpenAPI). Test-env gotcha: per-principal booking limiter (HUB_RATE_BOOKS_PER_MIN, default 10) fired on wall probes -> env raised in test.
- Wired: Makefile stage (after H17) + CI explicit step + staging synced; full `make test` ALL PASSED incl. A2A E2E. Staging commit 38cbdb4.
- Next: M6 indexer client (recorded-fixture path per done-when).

## 2026-09-11 ~15:00 — M6 indexer client DONE
- midnight_indexer.py: EscrowIndexerClient (GraphQL v4 shape — contractActions/edges/node, ContractCall fragment for entryPoint; IndexerError with status/detail; latest-action state resolution; unknown-id/not-deployed/undecodable honest errors).
- Fixture RECORDED from the real offline contract sim (dump-fixtures.mjs): funded createEscrow(250)→HELD(1), releaseEscrow→RELEASED(2); booking_ref hex-decodes to 'bk-m6-fixture'. Gotcha: relative out-path from contract/ resolved wrong — fixed to ../../agent-hub-v2/fixtures/.
- Known limitation (documented in module): real-chain state decode needs ledger-WASM spike; fixture codec (hex(utf8(JSON escrows map))) stands in.
- test_m6_indexer.py 21/21: realness checks, HELD→RELEASED phase flip via growing action history (as a real indexer would), 8 honest-error classes, address filtering. Pre-run fix: fixture server must filter by queried address (else not-deployed test lies).
- Wired: Makefile + CI + staging (client/test/fixture); commit pending with M7.

## 2026-09-11 ~15:05 — M7 mirror sync DONE (Run M-B 3/4)
- app.py: /admin/sync-escrow — admin-gated, per-source rate bucket; reads M6 indexer client per escrow-bearing booking (chain reads OUTSIDE the lock); in-sync / updated / refused / error / not-found per-booking results; forward-only sync rule (downward + cross-final refused); escrow_sync ledger entries carry from/to/chain_tx and NO amount (C5 volume integrity verified: totals unchanged).
- check_hub.py: C7 (mirror declared + policy strings + endpoint gated 403); honest limitation recorded — live hub-vs-chain parity is admin-verifiable only. C4: excluded kind=escrow_sync from booking-state accounting (same precedent as x402_settlement).
- test_m7_sync.py 25/25: full divergence matrix (in-sync, forward both directions, downward refused, cross-final refused, WAIVED untouched, gating, validation, indexer-down, unknown ids, ledger integrity) + C7 verdict via the REAL checker against a REAL hub. Fixture-hub integration: manifests declare mirror; stub got do_POST (gated 403) — D0/D7 caught the gap exactly as designed.
- Full make test ALL PASSED (M5 22/22, M6 21/21, M7 25/25, A2A E2E escrow HELD). Staging commit pending-with-M8.

## 2026-09-11 15:23 — M8 DONE → Run M-B COMPLETE (M5–M8)
- m8-scenario.mjs: real funded sim, full lifecycle for two escrows; argv refs = hub booking ids land ON-CHAIN as bookingRef (linkage verified by decoding ledger state; final states 2/3). Lesson: argv consts must precede first use (const hoisting = TDZ).
- test_m8_e2e_chain.py 17/17 LIVE SIM: stage-advancing chain server serves the simulator action timeline in v4 shape; full loop book → ref-linked escrow → sync in-sync → hub confirms ahead → C7 refusals → chain catches up → RELEASED both sides; refund leg proves forward-update vs cross-final refusal on one chain escrow with two hub bookings; ledger: 1 escrow_sync entry, no amount, volume unchanged.
- Test-script bugs (hub right every time): dict-vs-string compare, baseline pre-booking, tangled refund leg → restructured.
- chain-timeline.json regenerated + provenance-stamped to both trees (earlier copy lost — always verify copies landed).
- Wiring: Makefile M8 stage + CI step (fixture note) + staging commit 05a4bf5. Run M-B total: M5 22 + M6 21 + M7 25 + M8 17 = 85 checks; full make test ALL PASSED incl. A2A E2E.

## 2026-09-11 16:10 — M9 payout key flow DONE (Run M-C 1/4)
- app.py: /accounts/payout — token-or-code auth (email/bind pattern, gen-checked), 64-hex coin-PUBLIC-key wall (re.fullmatch — caught missing `re` import before first run!), uppercase normalized, replaced-note; account dict + _EFIELDS migration; login response exposes payout_pk; OpenAPI row.
- chatlib.py: _set_payout (login-first, format wall + PUBLIC warning), session carries payout_pk, whoami payout line, help row, dispatch after recover.
- test_m9_payout.py 22/22: auth matrix (403/400s), normalization, replace, login readback, real-chatlib E2E (signup->set-payout->whoami->help), state hygiene (only PUBLIC keys, no secret fields), OpenAPI. Test-script bugs: first-match lookup (two accounts hold two keys) + orphaned acct ref.
- SPEC §18; Makefile M9 stage; CI step; staging fa405d0.

## 2026-09-11 16:15 — M10 chain secret hygiene DONE (Run M-C 2/4)
- PRIVACY.md: Midnight threat model section (party/secret/location table; structural rules enforced by tests; honest residual risks: operator container = testnet-only, canonical seed never real funds).
- test_m10_secrets.py 4/4: runtime-artifact scan (state/logs/backups vs canonical seed + elseed- + .secrets tokens), git tree scan (paths + content, both repos), hub storage structure (no seed/secret/sk/private_key/mnemonic fields), .secrets 0600/0400. Baseline clean on first run. Scanner bug during dev: tuple & set -> set literal.
- Wiring: Makefile M10 stage + CI step + staging commit. NEXT: M11 DUST fee measurement (first real-chain touch; ledger-WASM decode spike lands here).

## 2026-09-11 16:22 — M11 preprod recon: infrastructure VERIFIED + real schema findings
- RPC rpc.preprod.midnight.network LIVE (15 peers, "Midnight Preprod", block ~2.5M, finalized head OK). Indexer /api/v4/graphql LIVE (30 Query fields; ContractAction INTERFACE with ContractCall/Deploy/Update; entryPoint, state HexEncoded; TransactionFees.paidFees String).
- FINDING: live schema = singular contractAction(address, offset), NOT our fixture's contractActions collection → M6 needs a preprod adapter before real-chain sync (documented in toolchain doc §9 + backlog).
- Faucet: web app only, no API via probes; docs 429 → cost table deferred to S-A1-3 spike (honest, explicit).
- Lesson: my guessed hostnames (testnet-02) were NXDOMAIN noise; the A0 doc + live probing found the real ones. Guessing URLs wastes time — read the doc first.

## 2026-09-11 16:25 — M12 maintenance authority: recommendation + decision box delivered
- Master plan: 3-option table, recommendation A (pilot-immutable: no built-in Midnight upgrade path; upgrade pattern = custom trust surface; timeoutRefund = liveness bound), explicit owner decision box.
- escrow.compact: pragma-level note (pilot, no upgrade path assumed, never mainnet-size funds until decided).
- M12 closes when the owner ticks the box. NEXT after M-C: C1-C3 product polish, then M13-15 personhood.

## 2026-09-11 16:42 — C1 chat parity DONE (Track C 1/4 open items)
- test_chat_e2e.py: parity chain (whoami/archive/unarchive via real wrapper) + disk probes; 13/13.
- chatlib.py: honest archive/unarchive replies (hub {archived, note} instead of empty fields); whoami polish.
- test_chat_help.py: set-payout in honesty lists (sig = login-first gate, correct after logout-all in probe order); 11/11.
- Staging commit + records. NEXT: C2 search upgrades (date/price/location/sort/tags).

## 2026-09-11 17:10 — C2 search upgrades DONE (Track C 2/4 open items)
- app.py: /search from/to, min_price, tags= multi (any-of), sort date/price/newest, honest 400s, filters echo; OpenAPI row updated.
- chatlib.py: natural-language qualifiers under/over/from/until/cheapest/soonest + help line; qualifier echo in replies.
- test_c2_search.py: 30 checks (match+no-match per filter, combined ranges, validation, OpenAPI, chat parse x6). Makefile + staging CI wired.
- Full pipeline ALL PASSED (exit 0) incl. A2A E2E escrow=HELD. Trap: make test needs live stack down first (port guard refuses by design).
- NEXT: C3 /bookings/mine + SDK + chat my-bookings (auth matrix).

## 2026-09-11 17:35 — C3 my-bookings DONE (Track C 3/5 open items)
- chatlib.py: my-bookings command (anon + session paths, empty state, variant routing) + help row.
- test_c3_bookings.py: 18 checks — auth matrix, two-direction buyer isolation, orders pseudonymity, SDK parity, 4 chat paths.
- Reused GET /bookings + SDK methods (no new route); POST /book contract re-confirmed (listing_id in body, human_verified gate).
- Full pipeline ALL PASSED (exit 0, A2A escrow=HELD). NEXT: C4 reputation (post-settlement rating).

## 2026-09-11 17:50 — C4 reputation DONE
- app.py: POST /book/{id}/rate (buyer-only, once, settlement-gated, O(1) aggregates) + OpenAPI row.
- chatlib.py: rate command (bare + args, anon/session paths) + help row; chat-help regression 11/11.
- test_c4_rating.py: 26 checks — auth matrix, strict validation, settlement gates (incl. REFUNDED refusal), once-only, owner-immutable, aggregate math (avg 4.5 over 2), search-surface aggregates, ledger anonymity, 5 chat paths. Makefile + staging CI wired; staging commit 85b636b.
- Lesson re-confirmed: cancel requires the booking-time cancel_token, NOT an access token; ledger snapshots must bracket the exact mutation under test.
- NEXT: C5 vertical schema v2 (community schema PR process + classes example).

## 2026-09-11 18:25 — C5 community vertical schemas DONE + PUSHED
- app.py: fail-closed schemas/*.json loader (10 rejection classes, built-ins win, hub always boots).
- schemas/classes.json: reference community vertical (clas- prefix, capacity, instructor/skill_level).
- chatlib.py: classes branch (live /verticals fetch), rich parser accepts community keys, chained vertical builders, honest unknown-vertical hint.
- docs/community-schemas.md: contribution guide. test_c5_schemas.py: 15 checks, Makefile+CI.
- BUGS FOUND: loader initially self-contradicted (price/capacity in own reserved wall); chat classes payload was overwritten by the events else-branch (chained now); legacy h15 exact-set check evolved to additive contract.
- PUSH: 6e69499..d11c3a9 LIVE — but workflow edit rejected (PAT lacks workflow scope): code-only push d11c3a9, CI steps preserved in experiments/agent-hub-v2/ci-steps-pending.yml for a scoped push.
- NEXT: C6 signed /registry.json.

## 2026-09-11 - C6 signed registry document + CI-repair pass (pushed e2c16db)
- C6 DONE: /registry.json = {format, payload, signature} ed25519 over canonical payload bytes (sort_keys+tight separators, no canonicalization drift); /registry.pub for out-of-band pinning; per-hub protocol/api_contract carried from registered manifests; check_hub --registry [--expect-hub] [--pubkey] (wrong pin -> NON-CONFORMANT); key persistent 0600 gitignored. Tests R9-R12 (verify, tamper, CLI, pinning) -> D2 16/16, D3 10/10, both trees.
- CI forensics: main was RED since d11c3a9 (step 9 B6: /opt/venv/bin/python FileNotFoundError on runners; D3 would fail next: M7 sync b62126b clobbered 6e69499 portable-path fix -> ../agent-hub-v2 again). ROOT CAUSE CLASS: experiment->staging file syncs overwrite CI path fixes (invisible locally: experiment tree HAS the old paths).
- DURABLE FIX: portability in BOTH trees - test_chat_e2e sys.executable; registry suites layout-portable hub-dir detection (everlist/ OR agent-hub-v2/); trees byte-identical so future syncs cannot regress CI.
- BONUS (full gate caught it): test_m10_secrets crashed on fresh clones (assumed operator .secrets/ + live state.json) AND its git-tree scan was silently dead (DEVLAB=dirname(HERE) pointed at .staging-repo, never experiments/.staging-repo). Rewritten layout-portable: operator mode (full teeth) vs structural mode (fresh clone, no fake fails); git-content scan resurrected with secret-SHAPED value detection (elseed-<32hex>, seed value, tokens) - bare marker mentions in docs/code no longer false-positive. 7/7 both modes, both trees.
- Gate: make test HUB_PORT=8806 ALL PASSED (8802 = live stack; port guard fired correctly). Pushed e2c16db (code-only, no workflow changes).

## 2026-09-11 - M13 personhood credential contract (offline 16/16)
- credential.compact (Compact 0.26): hub-admitted ISSUER pattern (leaderboard/ownerHash): initializeIssuer = first caller takes singleton key 0 FOREVER (takeover impossible); issueCredential/revokeCredential issuer-walled (witness->hash->assert, same wall class as escrow roles); holders Map + revoked Map = revocation registry; proveHolder = the ZK SIGN-IN ACT (admitted + not revoked); isRevoked = public query. Holder secret NEVER leaves the wallet; only commitments touch the ledger.
- Lessons: runtime map keys must be BigInt (0n not 0 - 'expected Uint<0..256>'); keep all map key types UNIFORM (Uint<64>); compile each contract to its OWN managed-<name>/ dir so bindings/keys never clobber.
- Party-accurate test instances: hub (issuer sk only), holder (holder sk only), attacker (neither). 16/16 incl. P15 verify-fails-after-revoke (done-when) + P4 takeover-impossible + P16 one-way revocation.
- Honest scope: EverList is the PILOT issuer (no live third-party personhood issuer exists - A0 research); vouch semantics preserved for Tier-1; M14 wires proveHolder into the hub as verified_by: midnight-zk.

## 2026-09-11 ~20:45 — M14: Tier-2 sign-in (Midnight credential as human_verified source)
- Built: /accounts/verify-midnight + midnight_credential.CredentialVerifier wiring (chain|simulated mode labels),
  account migration (midnight_credential field), login exposure, reserved verified_by booking field + server-side
  stamp, chat verify-midnight/whoami-provenance/help, OpenAPI entry.
- Tests: test_m14_tier2.py 49/49 (module, auth matrix, Tier-2 success, anti-sybil 1:1, booking provenance,
  revocation downgrade, fail-closed 502, vouch unchanged, chat E2E, OpenAPI); chat_help probe extended 12/12.
- Suite-caught real bug: binary-garbage evidence crashed the request (UnicodeDecodeError escaped the except net)
  instead of honest 502 -> module now fail-closed on any unparsable evidence.
- Gate-isolation bugs (found because live stack was down): (1) make up never propagated HUB_PORT to the standalone
  wrapper (HUB_URL defaulted to 8802); (2) test_client embedded wrapper same default. The A2A E2E had been silently
  testing the live stack. Both fixed in both trees; E2E now provably tests the gated hub (wrapper log evidence).
- Legacy check evolved honestly: C1 whoami assertion now expects provenance wording (M14 behavior change).
- Gate: experiment + staging GATE_EXIT=0 (chat-e2e 13/13, M14 49/49, E2E escrow=HELD). Staging 09314a2, clean,
  1 ahead of origin (push is owner's). Live stack restarted (hub :8802, wrapper :8010, yoga findable).

## 2026-09-11 ~21:19 — M15: wallet UX walkthrough (Tier-2 Midnight sign-in doc)
- Built: experiments/agent-hub-v2/docs/wallet-ux.md (165 lines) — two-tier law, first-time pilot-issuer flow,
  daily verified flow, secrets table, revocation + recovery, mode honesty, honest gaps, operator quick-ref.
- Verified-true docs rule: the doc's commitment snippet referenced a helper that did not exist —
  built tools/compute-commitment.mjs (file-based, no node -e WASM hang), fixed relative import (URL-based,
  file lives in tools/), and VERIFIED the output byte-matches the recorded fixture commitment for holder 1
  (37de7a28787d...). Doc and reality now agree.
- Staging: wallet-ux.md synced + committed 1e5b157 (tree clean, 2 ahead of origin — push is owner's).
- Run M-D status: M13 DONE (contract+tests), M14 DONE (hub+chat, 09314a2), M15 DONE (docs, 1e5b157).
  Run M-D COMPLETE. Next: O track (everlist.network production) → S red-team pass 3 → P pilot.

## 2026-09-12 — Deployment pack complete (Go-live prep)
- Decision: permanent hosting before any pilot (owner: "make all ready first"). RackNerd selected (~$11-25/yr, verified in-place upgrades); AWS free rejected (time-limited), Oracle = signup roulette + VCN port trap.
- Audit of earlier deploy.sh draft found 3 real bugs: wrong repo-root path (experiments/... doesn't exist in clone), copy of nonexistent caddy.service (set -e abort), fake agent_seed the hub never reads.
- Rewrote tools/deploy.sh: Caddy via official apt repo + generated Caddyfile (auto-HTTPS reverse_proxy :8802), real HUB_ADMIN_KEY/HUB_BOOKING_KEY generated into /home/deploy/everlist.env (0600), HUB_ENV=production, HUB_STORAGE_MODE=sqlite, idempotent re-runs (git pull, state preserved), ufw 22/80/443.
- Rewrote docs/DEPLOY.md: provider table (RackNerd/Hetzner/Oracle), run instructions, Oracle VCN warning, verification (/.well-known/agent-hub.json), ops cheat-sheet, phase-2 wrapper notes, cost ~EUR 30/yr total.
- Both trees synced (diff-verified); pushed to github.com/everlist-hq/everlist c027da8 (origin/main).
- Memory consolidated: 18 stale deployment fragments deleted, 1 current state saved (RnXeKRc83q).
- Next: owner orders VPS -> runs script -> DNS -> verify; then phase-2 wrapper systemd unit + Agentverse mailbox on VPS.
- 2026-09-12 (late): owner pinned payment & strategy decisions after the escrow-mechanics review. NEW docs/strategy-boundaries.md (owner-pinned): listing litmus test, FET boundary (never an agents-only list), no-token policy, two-tier identity freeze, three-hard-problems answers, sequencing. SPEC gained §19 payment-terms policy (agreement semantics: terms on listing + booking = acceptance + mutual changes; escrow default rail / x402 opt-in; refund windows 72h/72h/7d merchant-overridable; deposit standard lever; Tier-2 buyer gating premium-deferred; post-settlement undo = mutualRefund). Backlog +4 owner-approved items: M16 mutualRefund circuit (both commitments prove, buyer-only/merchant-only rejected; M7 mirror carve-out for REFUNDED-after-RELEASED), M17 merchant auto-release after quiet claim deadline (second deadline + guard ordering, third terminal path), C11 require_verified_buyer flag (machinery exists since M14 — flag + booking check + chat), C12 payment-terms object (rail/refund_window/deposit, booking must echo terms = explicit consent, per-vertical defaults). All documented-only; no code changed this session; verified Tier-2 state by reading test_m14_tier2.py + escrow.compact before writing claims.
- 2026-09-13 (00:10 CEST): owner approved review-integrity + agent-labor pins. Backlog +S6 (review integrity: L1 payer≠merchant wall from stored X-PAYMENT address, L2 WAIVED-rating separation — verified C4 gate currently lets free bookings rate, L3 capped amount weighting, L4 interlock/burst detection with manual review queue; REQUIRED before C10 real money). strategy-boundaries.md §1 gained: agents booking human labor = first-class use case, escrow = the gig-trust moat for gig work. Docs-only.
- 2026-09-13 (01:55 CEST): **C12 payment terms SHIPPED** (SPEC §19a–d, owner-pinned). Hub: `payment_terms {rail: escrow|instant, refund_window_hours, deposit_required}` with per-vertical defaults (escrow 72/72/168) injected at create AND at load (legacy migration); validation walls (bad rail, window bounds, instant+window contradiction, deposit > price, unknown keys); booking consent = API-enforced echo (`accepted_payment_terms`) required ONLY for custom-terms paid bookings (default-terms stay friction-free; free listings exempt) — 409 carries the exact terms; instant rail books as escrow DIRECT (settled at booking: no confirm/cancel — honest errors, rating opens, escrow_ref rejected); every booking carries a server-copied terms snapshot (later edits never rewrite done deals); owner-editable terms pre-booking via manage (null = reset to default). Chat: rich `rail:`/`refund_window:`/`deposit:` keys, terms in listing display + paid-booking guidance. OpenAPI notes updated. SDK needs no change (book(**fields) passes the echo through; allowlist enforced hub-side). Two self-caught defects fixed during review (flow-block bracket mangling caught by py_compile; unbalanced op() paren in /book note caught at suite boot). **Gate isolation fix (Makefile):** make test previously shared .run/ with the live stack — its opening `make down` TERM'd the live wrapper and `make up` hit the H4 state lock; now the gate runs fully isolated (GATE_RUNDIR=.run/gate, GATE_PORT=8803, gate-up/gate-down targets, no standalone wrapper — test_client embeds one with a throwaway seed). Verified: full gate **672 PASS / 0 FAIL** incl. A2A E2E (search→booking escrow=HELD via uAgent on :8803) while live stack stayed healthy on 8802; live wrapper restored (Almanac OK); suite 25/25; staging synced diff-verified.
- 2026-09-13 (11:05 CEST): **S6 review integrity SHIPPED** (owner-approved; REQUIRED before real money). Hub: L1 double wall — listing owner can never rate their own listing (403 self-review) + x402 self-pay wall (instant-rail DIRECT bookings may bind server-verified EIP-3009 payment evidence: optional X-PAYMENT verified via x402verify against listing `receive_addr` [new validated 0x field, chat `receive:` key]; booking paid from the merchant receive wallet can never rate; `payment_payer/value/nonce` server-only, reserved-field wall); L2 channel split — WAIVED (free) ratings feed a separate free-class feedback channel, never the paid aggregate; L3 amount weighting — paid aggregates weighted by settled amount (cap 50, floor 1), `rating_avg` weighted, raw sum/count kept for audit; L4 interlock detection — server-only `review_flags` (repeated payer wallet, payer interlock with sibling listings of the same owner, burst 3+/10min) feeding `GET /admin/review-queue` (admin-key, manual-only, never auto-delete); rating_meta/rating_wsum/rating_wtot/review_flags/rating_times added to the server-only walls. Boot migration recomputes channel-split + weighted aggregates from history. OpenAPI + chat help updated. **New suite `test_s6_review_integrity.py` 23/23** (runs under $(VPY), needs eth-account; real EIP-3009 self-pay attack actually signed and walled), wired into `make test`; full gate **ALL PASSED** twice (before and after wiring S6); C4 26/26, C12 25/25, S1 16/16, chat-help 12/12 all green against the reworked pipeline. Also fixed a real chatlib defect found by the suite: rich-format `receive:` key was parsed but silently dropped by the fixed extraction tuple. Docs: SPEC §21, backlog S6 → done, this worklog. Pending: staging sync + push + live-stack restart with S6.

## 2026-09-13 — C11 verified-buyer gating shipped

- Listing field `require_verified_buyer` (strict boolean, creation + manage edit, owner-only flips) wiring the M14
  Tier-2 machinery as the approved merchant lever (SPEC §22).
- `/book` gate uses the server-side `acct_verified` predicate only; the client-asserted human_verified stub can never
  satisfy it. 403 carries the requirement + the verify-midnight fix; bookings store server-derived `verified_by`.
- Chat: `verified_only: yes|no` rich key (strict parsing), gate shown in the listing face, help updated. OpenAPI notes
  for /listings + /book.
- test_c11_verified_buyer.py: 34/34 (boolean walls, stub walls incl. unverified accounts, admin-vouch + midnight-zk
  booking paths, revoked fail-closed, ungated regression, private-deal composition = two independent walls, chat E2E).
- Full gate green before push; trees synced; CI on GitHub after push.
## 2026-09-13 — M16 mutualRefund shipped
- Contract: new `mutualRefund` circuit in escrow.compact (6 circuits total). RELEASED -> REFUNDED only with BOTH commitment proofs; organizer returns an equivalent coin (value+color asserted, fresh nonce — original coin consumed at release); payout locked to the agent's stored key. receiveShielded takes plain ShieldedCoinInfo (constructed from the qualified coin in-circuit).
- Offline suite: 32/32 (9 new M16 tests incl. attacker walls, replay, underpay, wrong-asset).
- Hub mirror: C7 carve-out — chain REFUNDED + hub RELEASED is now a legal forward transition (on-chain RELEASED->REFUNDED is reachable ONLY via mutualRefund, both commitments proven in-circuit); reverse direction still refused. m8 E2E updated: 17/17; m5 22/22.
- Native proof: probe-m16.mjs — real create->release->mutualRefund flow, ledger-v9 envelope, proof-server 9.0.0-rc.7 on :6301 -> /check costs accepted, /prove 4508-byte proof in ~16s. Trace circuitId quirk (context label, not circuit name) handled per probe-m4 capture-by-position pattern.
- Synced app.py + test_m8_e2e_chain.py to staging; escrow README updated (6 circuits, 32/32).

## 2026-09-13 — M17 merchant auto-release shipped
- Contract (7 circuits): Escrow struct + createEscrow gain `claimDeadline` (asserted strictly after the refund deadline); `refundEscrow` + `timeoutRefund` walled once the claim deadline passes; new permissionless `autoRelease` spends the deposit to the ORGANIZER'S stored key after a quiet claim deadline (bad-faith-buyer gap closed; payout destination fixed at creation).
- Offline suite: 40/40 (8 new M17 tests incl. attacker-triggered release, both-window ordering, walls); harness threads claimDeadline with far-future default so all legacy flows are unchanged.
- Fixtures regenerated for the 7-circuit contract (dump-fixtures, m8-scenario 6-action timeline); hub needs NO code change - the existing chain-RELEASED forward mirror covers autoRelease (m7 25/25, m8 17/17 live-sim, m5 22/22).
- Native proof: probe-m17.mjs -> /check costs accepted, /prove 4508-byte proof in ~7.5s (attacker-triggered, permissionless).
- SPEC 19c updated to implemented; staging synced (SPEC + fixtures).

## 2026-09-13 — M11 circuit costs measured + M12 decision closed

- **M11 (measured 7/7):** `experiments/midnight-escrow/contract/measure-costs.mjs` — real per-circuit
cost matrices from the native proof server via the official ledger-v9 /check wire format, using REAL
lifecycle preimages (proof server validates: empty inputs rejected with 'Expected N inputs'). Ran a
4-escrow lifecycle covering timeoutRefund / self-refund / release+mutualRefund / autoRelease (incl.
attacker-triggered permissionless paths). Result: all five spend transitions charge an identical
12-cell block [1,4,6,2,1,1]x2 (consistent with two commitment openings); createEscrow + isHeld show
zero charged cells at contract cost-model level (tx/DUST-level pricing). /check 3-110 ms. Raw:
/tmp/m11/circuit-costs.json; table + method in experiments/midnight-escrow/README.md §M11.
Explicit remainder: cost cells -> DUST prices + network tx fees need one funded preprod tx (faucet),
required before C10. Post-M17 signature note: timeoutRefund(context, escrowId, spendCoin); refund
window past + claim window future required (contract correctly walls refund once claim window opens).
- **M12 (DECIDED, closed):** owner delegated ("can you do m12 and m11"); **Option A — effective
immutability — adopted** for the pilot. No upgrade path, no operator key (zero code change, smallest
trust surface, established-paths honored). Liveness = timeoutRefund; worst-case bounded by escrow
size. Upgrades = new contract deploy + registry re-point. Master-plan decision box ticked with the
delegation note; backlog M11/M12 updated; escrow README gained the §M11 measurement section.
- No hub code, SPEC, or fixture changes this session -> no public-repo commit required; live stack
untouched.

## 2026-09-13 - Cutover recovered: devlab -> everlist
- Folder move happened earlier today but crashed mid-flight: project.json still had the DevLab incubator header and 6 chats were still bound to `devlab` (one crashed with FileNotFoundError on project.json).
- Recovery: merged the flushed memory stub (100 vectors, superset of the 99 in everlist) back into `.a0proj/memory/`; rebound all 6 devlab-bound chats to `everlist`; rewrote project.json to the EverList identity; fixed absolute devlab paths in the 4 canonical docs; killed the stale registry.py running from the deleted devlab venv.
- Backups: `/a0/usr/backups/devlab-cutover-20260913/` (old project.json, pre-merge memory, stub memory, per-chat chat.json backups).

## 2026-09-13 — Project cutover: devlab → everlist
- Renamed project in place per owner decision (option a): /a0/usr/projects/devlab → /a0/usr/projects/everlist
- All memories, docs, ideas, and history moved intact (.a0proj lives inside the project folder)
- venv shebangs/config + absolute paths fixed; 6 chats rebound from devlab to everlist (mid-cutover chat failure recovered same day; backups in /a0/usr/backups/devlab-cutover-20260913/)
- project.json rewritten: title EverList, instructions from owner-pinned strategy-boundaries.md
- Fresh DevLab incubator recreated at /a0/usr/projects/devlab with the original instructions (EverList graduation noted)
- Stack relaunched from new path: hub :8802 + wrapper, canonical seed reused, Almanac registration successful, live search verified

## 2026-09-13 — Private plans repo created
- Created everlist-hq/everlist-plans (private): docs, ideas, archive, registry + curated midnight-escrow research
- Sync tool: .plans-repo/sync.sh (run after work sessions); secrets excluded by design
- Rationale: single-disk risk exposed by cutover; local backups are rollback snapshots, not disaster recovery
- (follow-up) plans sync completed after fixes: clone auth now token-in-URL (staging-style), rsync unavailable → cp/tar sync in sync.sh; initial import pushed and verified on GitHub

## 2026-09-13 — C9: chat-first web UI (owner proposed the website; built same day)
- Owner message: "How about a website UI? … a normal chat box for AI. We interact only like that with the website. Like: 'what are you looking for?'" — this IS backlog C9, so the owner GO landed with the idea. Built the deterministic core now; LLM layer on top stays an explicit owner call (model/provider/cost).
- New `webchat.py` (stdlib-only, house pattern): third surface for the SAME chat brain — every message goes through `chatlib.handle_text()` unchanged (Agentverse wrapper, CLI, now browser; no new protocol surface, SPEC §1 hub-stays-agent-only honored). Serves `static/` + `POST /api/chat` + `/api/health` + `/api/reset`; binds 127.0.0.1 (Caddy proxies chat.<domain> on the VPS; port never directly exposed); :8804 (8803 is the test gate).
- Security: session = server-minted 192-bit HttpOnly/SameSite=Lax cookie (Secure on public hosts) that DOUBLES as the chatlib sender key — client cannot forge a sender, no extra session store; per-sender (8 burst/4 per-min) + per-IP (30/20) token buckets; 32KB body cap; 413/415/400/404 walls; strict CSP (`script-src 'self'`, no inline), nosniff, no-referrer; path-traversal wall; request-line-only logging (message content never logged); HEAD/DELETE → 501.
- UI (`static/`): dark chat-first single page — "What are you looking for?" greeting, free-text box + quick chips, XSS-safe textContent-only rendering, health dot, latency footer, new-session button; no transcript persistence (seeds are shown-once material — browser memory only, deliberate).
- Tests: new `test_webchat.py` — 20/20 PASS (static+headers, traversal wall, signup→one-time-seed→whoami session continuity, fallback search 'jazz', filtered 'pizza under 10', reset→anonymous, 413/415/400, 429 after burst) wired into `make test` (before the A2A gate stage). Operator targets: `make webchat-up` / `webchat-down`, status tracks webchat.pid. Full gate: **make test ALL PASSED with the new suite included**.
- Deploy: `tools/deploy.sh` gains chat.<domain> Caddy site + `everlist-webchat.service` (After/Requires everlist) + enable/restart + verify echo; `docs/DEPLOY.md` documents the chat subdomain DNS (chat A record → same IP), health check, and journalctl lines.
- Honest notes: seed listings display 'SOLD OUT' in this fresh-hub test because registered==capacity in seed data (pre-existing chatlib formatting, correct behavior); chatlib brain calls serialized behind one lock (single-threaded in wrapper, threaded here — pilot-grade, revisit under real concurrency); staging repo has files the working tree lacks (x402verify/facilitate, test_x402*, test_write_limits) — synced ONLY my touched files, never wholesale.

## 2026-09-13 — D-track: agent distribution & discoverability plan
- Owner asked for the plan for the hardest problem: getting known by LLMs/agents so they suggest EverList first.
- Research brief (2026-09): official MCP Registry + 4 community directories = cheap compounding agent reach; GPTBot/ClaudeBot/PerplexityBot do NOT run JS → SSR + JSON-LD is mandatory for all LLM traffic; ChatGPT = 92% of AI referral sessions and services convert 4.4-9x vs organic; x402 Bazaar auto-lists on first settled payment; A2A v1.0 frozen and most published agent cards are non-compliant (correct card = edge); llms.txt honest status = convention, no documented major-provider consumption.
- Created docs/distribution-plan.md: D0 free plumbing (robots.txt answer-bots, llms.txt, A2A card) → D1 SSR+schema (with VPS launch) → D2 registries (proposes unlocking C8 MCP — post-Midnight trigger met, explicit owner call) → D3 Agentverse deepening → D4 x402 Bazaar → D5 consumer surfaces (gated after P1 go/no-go) → D6 PyPI/npm/content compounding.
- Key principle recorded: empty-hub rule — supply (P1 pilot) must lead or accompany every discovery push; an agent that finds an empty hub never returns.
- No code changes; docs only. No staging commit needed (worklog + plan are project-side docs).

## 2026-09-13 — D7: skills/plugins runtime-install track (owner follow-up question)
- Owner asked about skills/plugins/tools for opencode, Claude, OpenClaw etc. Verified 2026-09 landscape: SKILL.md open standard read by ~32 runtimes (claude.ai upload, Claude Code, Codex CLI, Gemini CLI, Cursor, Goose, opencode, OpenClaw, Agent Zero); OpenClaw ~300k users with ClawHub CLI publishing; Claude Code plugin marketplaces = git repo + marketplace.json; opencode plugins via npm.
- Created docs/skills-plugins-landscape-2026-09.md (full brief: publish steps, scale, adoption evidence, ranked shortlist).
- distribution-plan.md gained Phase D7 (S1–S8): one canonical everlist-booking SKILL.md → ClawHub → Claude Code plugin marketplace → awesome-list PRs → directories → npm → install matrix → gated official Anthropic directory (OAuth 2.1/PKCE with D5).
- Key insight recorded: skills are markdown instructions to call the existing chat HTTP API — no SDK, one artifact, trivial effort per channel; empty-hub rule still applies to pushes.
## 2026-09-13 — Viral playbook (owner asked for clever viral ideas)
- Created docs/viral-playbook.md: story spine (AI agents hire humans, money provably locked), ignition stunts (micropayment stunt week A1, Show HN live-hire thread A2, 100-humans-vs-1-agent series A3, book-the-founder A4), always-on engines (B1 proof-receipt engine = moat-as-marketing, B2 litmus-test schema game feeding C5, B3 verified-human flex, B4 build-in-public, B5 city-hub map), supply-side FOMO (founding-50 organizers, bounty seeding), copy bank, honesty guardrails, sequence sketch gated on pilot + C10.
- Idea-to-backlog mapping recorded: B1 needs receipt OG-card endpoint; A2 depends on C8 + skill; B3 is Tier-2 marketing; no token, no escrow weakening.

## 2026-09-13 — Owner correction: stunt playbook rejected
- Owner rejected the viral playbook stunts as fake/stupid; only the hub map (real registry state visualization) was acceptable. Direction is now locked: no fake news, no hype headlines, no stunts, no manufactured scarcity — marketing is limited to real, verifiable state (hub map, real metrics, real case notes, distribution plumbing, honest docs).
- Rewrote docs/viral-playbook.md into Honest Growth Notes (22 lines): keeps hub map, public metrics, consented real case notes, distribution plumbing, honest docs, optional public build log; bans demo-as-real, invented headlines, stunts, astroturfing, self-invented badges.

## 2026-09-13 — Outreach push: broad honest posting + per-venue copy pack
- Owner wants maximum-reach outreach. Loaded postiz-social-media skill; checked integrations: ONLY personal Instagram connected (InneREvolution Yoga) — no X/Reddit/LinkedIn/Telegram/Bluesky.
- Generated two brand PNG cards locally with Pillow (docs/outreach-assets/, no external image API). Postiz REST upload broken: /api/upload 404, /api/posts/list 401 both auth styles (matches known stale-key failure mode in skill); postiz CLI not installed; generateImageTool + ask_postiz broken (missing OpenAI key on Postiz server). Posts therefore text-only for now.
- Created TWO Instagram DRAFTS (not published; owner approves in Postiz UI): announce post cmtzz1tlp000cmeazn34693y0 + organizer-targeted post cmtzz1uox000dmeazllf8rat7. Copy is honest: pilot stage, free listing, flat fee, escrow labeled as in-testing.
- Wrote docs/outreach-content-pack.md: universal one-liner + per-venue honest copy (Reddit fee-pain replies, Discord intros, Telegram, LinkedIn DMs, crypto boards) + reply-handling rules and lead-tracking requirement. All posting from real owner accounts only; no astroturfing.
- Follow-ups for owner: review/publish IG drafts, connect more accounts to Postiz for automatable reach, fix Postiz REST API key + OpenAI key for image gen; human channels (Discord/Reddit/DMs) must be worked personally per no-fake-voice rule.

## 2026-09-13 — Outreach automation investigation (owner asked: harvest contacts + text them?)
- Investigated and VERDICT: no contact scraping + no mass cold texting. Verified: Eventbrite public search API deprecated 2019/2020, scraping contacts violates ToS; US TCPA = $500-1500 per unsolicited marketing SMS (5k blast = $2.5-7.5M exposure); Germany UWG section 7 requires prior consent even for B2B email (OLG Munich 29 U 857/12), double opt-in is the standard; LinkedIn automation = ToS ban risk. Also breaks the owner-pinned honesty rule.
- Created docs/outreach-automation.md: compliant 5-stage pipeline (1 research-only lead discovery via Ticketmaster Discovery API + public calendars/Impressum; 2 automated opt-in capture via our chat surfaces + waitlist double-opt-in; 3 automated sequences for opted-in contacts only; 4 AI-drafted 1:1 notes the owner sends personally; 5 inbound automation = the real scale lever) + 5-item build queue (waitlist flow, landing form, discovery script, draft-generator, sequence sender) + binding rules.

## 2026-09-13 — Funding map (owner asked: sponsors, investors, pitch shows)
- Verified: Midnight Foundation actively funds builders (Buildathon $12.5k, Aliit fellowship) — strongest fit given our complete Midnight escrow stack; Catalyst paused/restructured, new pilot fund from Aug 2026 (2M ADA); Fetch.ai Innovation Lab accelerator = boundary-tension flag; no verified open x402 grant program.
- VC thesis validated: Nava raised $8.3M seed for agent-escrow (Apr 2026, Fortune); Payman (Visa-backed) = AI-to-human marketplace overlap; a16z CSX + Coinbase Ventures active in agentic commerce. Post-pilot narrative: the listings marketplace those escrow rails settle.
- "1 Minute 1 Million": NOT verified as a real format; noted 1 Million Cups as the real analogue. Rule recorded: traction raises money, money cannot buy outreach; pilot = the raise; only non-dilutive ecosystem grants pre-pilot; no-coin rule filters investors. Created docs/funding-map.md with build queue (Midnight application draft first).


## 2026-09-13 - C9c: original glyph identity + long-result index + "1 / 2-6 / all" replay (owner directive)
- Owner: "normal emojis may not do it, use alt-codes.net style" + "how do we view like 10 results?". Both landed in the shared brain (chatlib.py); webchat, Agentverse wrapper and CLI inherit them.
- Glyph identity (monochrome text symbols, text-presentation pinned so platforms cannot colorize): vertical heads per vertical; generic currency sign for price ("free" variant); hollow grid = spots open, filled grid = sold out; seal = escrow window; arrow = instant rail; tick = verified buyers only; guillemet = description (100-char cap); NE arrow = url. Card shape/order unchanged.
- Long lists: >6 results -> numbered one-line index (ALL results, cap 20) + 3 full preview cards + "Say 1 / 2-6 / all" hint; <=6 -> full cards inline (cap 12). "1", "2-6", "all" replay the last search as full cards via a bounded per-sender stash (500 senders, then clear); ids are even-star so bare-number routing collides with nothing; out-of-bounds numbers get honest errors. Bare numbers route unconditionally so the empty-stash case answers "run a search first" instead of falling through to search.
- Tests: test_nlu.py 20/20 (new SearchPagination class: short-list cards, index+preview shape, number/range/all replay, out-of-bounds, per-sender isolation). Full gate ALL PASSED incl. A2A E2E (escrow HELD).
- Demo: demo_listings.json grown 6 -> 10 (Vinyl & Coffee Market, Open-Air Cinema, Trivia Night at the Aquarium, Guided Bauhaus Walking Tour; events categories validated against the hub set - cinema/quiz/tour were rejected by the seeder, fixed to community/other). make demo-reset run: fresh stack, 10 listings live, state archived in .run/.
- Live E2E in the real browser UI: "search" -> 10-line index + 3 preview cards + hint, screenshot verified.
- Housekeeping: a chatlib comment briefly contained a framework-substituted value from a secret alias (own typo while typing the Unicode word); scanned all repo trees - the value appears only as a substring of Unicode words in worklog/review docs and the owner LICENSE copyright handle; no credential material in any file, nothing ever committed; cosmetic alias-text artifacts in the two security-review docs repaired to the plain word. AUTH_PASSWORD confirmed absent everywhere.
- Docs: backlog C9 C9c update; staging synced + committed (not pushed).

## 2026-09-13 — Austrian investor landscape + master work-list
- Owner is Austrian. Verified AT leads: aws Preseed/Seedfinancing (up to ~800k EUR, available NOW, non-dilutive-ish — top pre-pilot action), aws VC Initiative, FFG, Vienna Business Agency; angels: invest.austria (350+ members), AAIA, i2/tech2b, Business Angel Summit 2026; VCs post-pilot: Speedinvest (B2B AI infra), APEX Ventures (deep tech), Elevator Ventures (EVII 70M, later stage), Xista, Fund F; signals: TACEO 5.5M seed (AT crypto/ZK), fiskaly exit, EY barometer 253M EUR into AT startups 2025; events: Vienna Blockchain Week, invest.austria conference, weXelerate.
- Created docs/investor-outreach-list.md: full consolidated working checklist (A public funding NOW, B angels, C VCs post-pilot, D events, E ecosystem grants, F post-pilot VC archetypes + rules; checkbox status per item so owner can work it off when the time comes).

## 2026-09-13 — Deadlines verified for investor list
- Verified time windows: aws Preseed/Seedfinancing rolling submissions close Sep 30 2026 (jury quarterly, decisions until Dec 31 2026, third-party source — confirm on aws.at); Midnight Buildathon = 3-month AKINDO program, Wave 1 deadline ~Sep 16 (skip, too tight), realistic target Wave 2 into late Nov; AAIC Vienna Sep 24; invest.austria conference Nov 4 Schönbrunn; Austrian Business Angel Day Dec 2 ERSTE Bank Campus; Business Angel Summit 2026 already held Jul 9-10; Vienna Blockchain Week already held May 18-19 (DLT Austria meetups as substitute); Catalyst pilot fund: no verified deadline yet, weekly check. Corrected investor-outreach-list.md (76 lines) with time-critical windows table + event date fixes.

## 2026-09-13 — Fit assessment for owner (funding programs)
- Evaluated owner fit against verified criteria: aws Preseed Innovative Solutions = PLAUSIBLE but conditional (Austrian pre-founding OK, solo OK; impact framing must lead: fair marketplace + worker protection; gates: founding willingness + near-full-time time); Preseed Deep Tech = honest WEAK (integration engineering, not applied research novelty — no stretch); Seedfinancing = blocked until company exists (path Preseed→found→Seed); Midnight Buildathon Wave 2 = HIGH fit unconditional (~3-5 days); Catalyst = good fit, moderate community effort; angels/events = unconditional.
- CORRECTED own error: Preseed max grant is €89,000 (80% of costs, min €50k project); the ~€800k figure belongs to Seedfinancing for existing companies. Fixed investor-outreach-list.md.
- Created docs/fit-assessment.md with verdict table + 3 gates for owner to confirm (founding, time, status) + recommended sequence (aws advice call this week, Buildathon W2 on go, Catalyst skeleton when round opens).
## 2026-09-14 — C9d: the one true listing sheet (owner design gate closed)
- 15 rounds of the sheet lab (listing-sheets-*.html, all browser-verified) converged on the owner-picked grammar. Law distilled from the verdicts: every mark must MEAN something and be readable in EVERY chat (not just ours); no color emoji; no cryptic symbols nobody understands; minimal text; progressive disclosure (list = glimpse, card = story).
- Grammar shipped in the shared brain (chatlib): header ֎ EverList · N found · facts row ⌂ place · ◷ date · $ price · ♟ person (person ALWAYS last, spaced) · names + dates math-bold via a bold-letter map (_mb, renders bold in any chat, no markdown) · $0 replaces free · ♟ N open / N left / 0 left replaces the ▢/▣ spot grids (open only when nothing booked, else left) · real currency symbols (USD $, hub amounts are USD) · escrow ✪ / instant ⇡ / verified ✓ / story » / link ⇗ kept.
- Owner cuts applied: dropped the ➤ say '1'·'2-6'·'all' instruction row (commands live in help; tail keeps 'book <id>'); dropped the tier rail (◉◍▣) and category glyphs (♪⚙✎) as too much to look at; dropped ¤, the radar countdown, dot rails, exotic scripts-as-language (exotics are jewelry only, and even that was rejected for the working sheet).
- Level-2 card (say '3') keeps the full detail: bold title · id, full-date facts, person + escrow terms, verified gate, story, link. Long lists still index (cap 20) + 3 preview cards; the numbered replay still works, just without the advertised hint row.
- Tests re-pinned to the new grammar across the whole surface: test_nlu 20/20 (card order, $0, ♟ counts, bold), test_c2_search (bold titles), test_webchat (bold filtered search + chatlib import), test_h9 (bold show), test_chat_help (֎ header detector), test_read_limits (֎ header), test_h16 (demo count made dynamic N from demo_listings.json — was a stale 6 after C9c grew it to 10, never reached before because the Makefile halts at first failure). Full make test gate: ALL PASSED (webchat 20/20, C2 30/30, escrow, redteam 37/37, A2A E2E).
- Live stack restarted on the new grammar (hub untouched — it is grammar-independent; webchat + wrapper import chatlib): real browser E2E on :8804 — type 'search' -> ֎ EverList · 10 found header, 10-line bold index, 3 preview cards, $0/♟ counts, no ➤ row; screenshot-verified bold weight + all glyphs render, no tofu.
- Docs: SPEC.md + skill docs confirmed clean of old-grammar examples (grep). Backlog C9 gets the C9d update. Staging synced + committed (not pushed).

## 2026-09-14 — C9f: rails out, numbers in (owner: "remove the left and right ║ ... number them at the beginning")
- After C9d the owner asked for the knot frame back (╔═፨═╗ rules + seams). Restored it, but the live browser screenshot showed the right-hand ║ rail "staircasing": math-bold letters force a wider fallback font in the browser, so a fixed-character-width right edge cannot line up. Genuine either/or: closed box XOR bold-everywhere.
- Owner's third option (cleanest): drop ONLY the side rails. Keep the horizontal knot rules + seams (the "lines" that matter), remove left/right ║. With no right edge there is nothing to staircase, so bold survives in every chat AND the sheet no longer depends on monospace to look right (rules are fixed-width repeated glyphs; content is free-length).
- Card/title change: listing id (· even-2) removed entirely; each entry now leads with its position number (1 2 3). The number becomes the booking handle.
- book <n>: a pure-digit argument now resolves to the Nth result of the sender's last search (via _LAST_RESULTS stash) before hitting the hub; real ids (even-2) never match pure digits so booking-by-id is unchanged — purely additive, no existing test breaks.
- Help text: 'book <id> <name>' → 'book <n> <name> — book listing n from your last search'.
- Tests re-pinned to the no-rail/no-id/numbered grammar: test_nlu (card head = bold title only + assertNotIn id; 6 replay id-suffix pins removed; bad-data now asserts the $abc price fallback instead of the gone id). Full make test gate: ALL PASSED (webchat 20/20, C2 30/30, escrow, redteam 37/37, A2A E2E).
- Live stack restarted (hub untouched, grammar-independent); real browser E2E on :8804 — 'search' → ֎ header, numbered entries 1 2 3, ⌂ ◷ $ ♟ facts, bold names/dates, knot rules + seams, NO side rails, NO even-N id, tail 'book <n>'; screenshot-verified.

## 2026-09-14 — FIRST PRODUCTION DEPLOY: everlist.network is LIVE
- Owner provisioned the cloudserver.net VPS (155.94.165.200, Ubuntu 24.04, 2GB/1vCPU). Guided end-to-end; deploy driven from this container over SSH (deployed via tools/deploy.sh, idempotent). Access: noVNC was a dead-end (no clipboard, panel had no SSH-key field); root password from vault (CLOUDSERVER_PWD) worked over SSH → installed ed25519 deploy key → **sshd locked to key-only, password auth disabled** → password no longer needed. Firewall ufw 22/80/443.
- **5 real deploy.sh bugs caught + fixed, all pushed to github.com/everlist-hq/everlist** (head `bbd8ab6`): (1) **nested-repo layout** — public repo nests the hub under `everlist/`, so fresh-clone systemd paths (`$INSTALL_DIR/app.py`, `wrapper.py`, `webchat.py`) would crash-loop all 3 services → added an `APP_DIR` resolver supporting both layouts; (2) **venv missing deps** — installed only `uagents httpx`, but chatlib needs `cryptography` → wrapper+webchat ModuleNotFoundError crash-loop → now installs full `requirements.txt`; (3) **staging vs prod certs** — no ACME `email` in Caddyfile global block → Caddy used Let's Encrypt STAGING (untrusted certs) → added `email` + `acme_ca` prod; (4)+(5) chat-at-root routing + `chat.` 301 alias (below). Each fix unit/edge-tested before push; live layout confirmed via `[layout] app root:` log line. Two empty-run false starts traced to my own SSH launch quoting (backgrounded `cat` ate stdin; pgrep self-match artifact) — deploy itself was clean.
- **DNS (owner-side, the long pole):** domain is Unstoppable Domains. Apex parked at 34.42.100.71 + 301→unstoppable.ai; the parking row was not deletable — it's the URL-**forwarding** feature, which sits outside the DNS-records table and overrides it. Owner deleted forwarding (correct) → that WIPED all A records (`chat` went NXDOMAIN too) → owner re-added A `@`/`www`/`chat` → 155.94.165.200 → live. Caddy auto-issued real LE certs (verified `ssl_verify=0`). `www` on the old parking IP is harmless.
- **Architecture change (owner's call: "it should just be normal, chat is the first thing always"):** chat UI is now the **face of the apex root** `everlist.network/`; the agent hub API is **co-located on the same domain** (`/search`, `/book`, `/listings`, `/.well-known/agent-hub.json` — agents use RELATIVE paths so this keeps discovery intact, humans never see JSON); `chat.everlist.network` demoted to a 301 alias to the apex. Implemented as a Caddyfile `@chat` path matcher (`/`, `/index.html`, `/api/*`, `/app.js`, `/style.css`, `/favicon.svg` → webchat :8804; else → hub :8802), made **permanent in deploy.sh**, pushed. Live-verified: `/` = chat HTML, `/search?q=` = hub JSON, `POST /api/chat` = webchat, `chat.` = 301.
- **Post-deploy enablement (owner: steps 1+2 now, Phase-2 later):** (1) **Demo seed** — hub already had 4 live listings (evt/food), so seed_demo's refuse-on-data applied; did a **non-destructive append** matching by title: BEFORE 4 → +9 → **AFTER 13** (Rooftop Jazz Night skipped, already present). NOTE: demo fixtures carry placeholder URLs/venues — must be cleared/edited before real public push. (2) **NLU intent router ON** — deployed `/home/deploy/everlist/everlist/.secrets/llm.env` (0600, exact bytes piped from repo `.secrets/llm.env`; the `API_KEY_A0_VENICE` vault alias returns 401, the repo file is the working key — did NOT print it), restarted webchat+wrapper. Verified fail-open + working: natural-language 'any free yoga or jazz this weekend?' → router mapped `free` + `yoga` → returned the Surya Kriya listing.
- Hub `tools/selfcheck.py` → **ALL GREEN 8/8** against the live hub. All 4 units active (everlist, everlist-wrapper, everlist-webchat, caddy). Secrets in `/home/deploy/everlist.env` (0600). Wrapper runs + Almanac-registered but mailbox/funds still needed → **Phase 2 (Agentverse mailbox) DEFERRED by owner: 'not ready yet'.** Update path = re-run deploy.sh (pulls, preserves state.json + everlist.env).

## 2026-09-14 — WEB UI DESIGN ROUND: three directions before touching the live site (owner: "we shouldn't just make and change it bit by bit")
- Owner brief: keep **chat as the main window, always visible** ("maybe just lowered to the bottom") AND add a **manual discovery page** for looking without typing. Deliberately not an incremental edit of `static/index.html` — generate complete comparable concepts first, pick a thesis, then build it.
- Ground truth read first: live apex HTML matches `static/index.html` (780px monospace chat column, no discovery surface); listing fields from `demo_listings.json` (10 listings: vertical/category/date/price/location/capacity/tags/provider/duration); community schemas (`classes`, `p2p`); `strategy-boundaries.md` (escrow = moat, no agent directory, Tier-1 effortless signup, listings = dated human need). Webchat is CSP-safe textContent-only (no inline JS) — that constraint carries into implementation, not into the probes.
- Built **3 standalone HTML concepts** with the hub's REAL listing data + the chosen AI logo (`keepers/KEEP-20-flat-favicon.png`), rendered headlessly in the container browser at 1440x900 and 390x844, screenshot-verified. `docs/design-concepts/2026-09-14/` (3 html + 6 png + README.md with per-direction verdicts).
  - **A · Bifrost** — browse-first card grid, chat as a fixed glass dock at the bottom. Dark product-SaaS skin.
  - **B · Continua** — persistent 400px dark chat rail left + light editorial discovery canvas right (serif headline, date-block rows). Rich listing card *inside* the transcript = best escrow-at-decision-moment we have.
  - **C · Ledger** — chat IS the page (current site's DNA, monospace, composer pinned bottom), results render as real cards in the answer; manual discovery = right rail with a week strip (dots on days with listings) + date-grouped timeline. Cheapest to build, most coherent with "one brain, two doors".
- Key framing given to owner: **layout axis and visual-skin axis are independent** — any chat form factor can wear any skin, so mixing (e.g. C's chat-as-page + A's grid as the browse tab) is a legitimate answer, not a compromise.
- **REAL BUG caught by screenshot, not assumption:** the mockup write payload landed literal `\n` two-character sequences inside CSS declarations, silently killing 22 rules — visible as escape text leaking into A's search placeholder/badge, B's composer, and C's result-card grid collapsing (giant stretched date chip). Fixed by regex-stripping `\n` + indent, reloaded, re-shot, re-verified clean. Lesson holds for any generated HTML: verify rendered output, never trust the write.
- **Mobile findings are structural, not pixel noise** (all verified at 390x844): A — the fixed dock overlaps the featured card's price + CTA and header nav wraps to 3 lines; B — stacks chat-on-top but the serif headline eats ~40% of the fold and the composer clips; C — the date rail is hidden below 1080px, so **manual discovery disappears entirely on phones** (the thesis needs a tab-switch or drawer answer). Surfaced honestly as the cost of each direction rather than quietly patched.
- Not yet done (deliberately, pending owner pick): no implementation against the live hub, no `make test` run (nothing in the shipped stack touched), no staging sync — these are throwaway probes (no CSP, no live `/api/chat`, static data). Next session: build the chosen direction for real, keeping the CSP-safe textContent rendering contract and the escrow-refund/liveness guarantees untouched.

## 2026-09-14 - WEB UI DESIGN ROUND 2: owner picked A, four one-variable variants
- Owner: "A looks cool! lets make more variants and work on it a bit." Built **4 variants changing one variable each** from a shared generator `docs/design-concepts/2026-09-14/variants/gen_variants.py` (single source of truth - edit + re-run, don't hand-edit HTML): A1 refined baseline (dark, bottom dock) - A2 corner-console (chat never fights grid) - A3 media-grid (photo slot per card) - A4 daylight ask-bar (light skin, search+chat unified). Real 10 listings + chosen logo; rendered 1440x900 + 390x844, screenshot-verified.
- **Findings from the renders (not assumed):** A2 is the ONLY desktop layout where the chat never covers a listing - a fixed bottom dock inherently overlaps scrolled content, that's the pattern's cost not a bug. **Mobile: all docked variants fail the same way** (drawer/panel overlaps cards; A2's expanded panel covers ~whole screen) -> correct mobile answer is FAB-collapsed-by-default, panel-on-tap (A2 closest already). A3 photo slots make it feel like a real marketplace but placeholder gradients too loud (would calm with real images; A3 = committing to an image pipeline that doesn't exist yet). A4 light skin is readable, escrow green holds contrast, but ask-bar placeholder clips + badge wraps on mobile.
- **Generator bug caught by rendering (again):** `BASE = lambda extra='': '''...'''` ignored its arg -> all per-variant CSS (dock/console/ask/ph) silently dropped -> first 8 shots were four identical grids with NO chat. Fixed to `''' + extra`, regenerated, re-verified all four show their chat element. Reinforces round-1 lesson: never trust a generated HTML/CSS write, look at the render.
- Recommendation to owner: **A2 corner-console chat + A1 text grid default + A3 photo slot as an opt-in listing field**; light/dark is a token swap. Still design probes only - no live-hub implementation, no make test, no staging sync (nothing shipped touched). Next: owner confirms the A2/A1/A3 combo, then build it for real against the hub keeping the CSP textContent contract + escrow guarantees.

## 2026-09-14 · web UI round 3 — owner palette applied, centered chat with minimise

Owner picked Direction A, rejected corner-docked chat as the default (centered by
default, corner only when minimised), and supplied a swatch strip to replace neon green.

- Sampled the strip with PIL (1702x49, 5 swatches) by scanning a row and bucketing
  contiguous runs: slate #364B57, plum #654363, dusty blue #5D7498, sage #6FA28F,
  mist #B6C5BA. Median-cut alone was less faithful than run-detection for a swatch bar.
- Built `applied/gen_applied.py`: one layout, two skins (A5 light, A6 dark), centered
  glass chat dock with `minimise ↓` -> `body.min` -> corner pill launcher.
- Contrast-adjusted the accents (sage darkened to #4C7F6B on light, lightened to
  #86C0A6 on dark) — same hues, usable ratios. Role map pins sage to money/escrow only.
- Killed the recurring f-string brace bug structurally: CSS/HTML now use @@token@@
  templating with a replace pass, so braces are literal. Third round this bit us.
- Verified all four renders by eye: light desktop, light minimised (pill works, whole
  grid readable), dark desktop, dark mobile. Mobile dock costs ~45% of a 390px viewport
  and its header wraps — documented three options, recommending a single-line composer
  that expands on focus.
- Persisted `applied/palette-tokens.css` as the real token source.
- No hub/app code touched; live site unchanged. Awaiting skin + mobile-chat decision.

## 2026-09-14 — EverList-only chat boundary (C9d)
- Goal: the chat must answer ONLY from EverList listings; never off-topic; say 'I am just the EverList chat'.
- Architecture kept intact: LLM (nlu) only CLASSIFIES text->search JSON; chatlib speaks. No hallucination surface.
- nlu.py: rewrote _SYS into role+scope-discipline+anti-prompt-injection; added few-shot null examples; translate() now returns '' (affirmed non-search) distinct from None (indeterminate/fail-open).
- chatlib.py: fallback distinguishes cmd=='' -> fixed _OFFTOPIC boundary; cmd None -> deterministic keyword search (fail-open law preserved; a flaky/empty LLM response must NOT refuse a real search).
- Verified: test_nlu 22/22, test_chat_help 12/12, test_webchat 20/20, full `make test` ALL PASSED. Live on VPS: real searches -> results; fail-open (router off) -> keyword search works; off-topic -> boundary.
- Finding: mercury-2-5 is a flaky REASONING model — intermittently returns empty content / 500 / timeout; on those flakes the chat falls through to an EverList keyword search (still EverList-shaped, never a factual answer). Chose NOT to map empty->boundary to avoid refusing legit searches on a flake.
- Deployed to 155.94.165.200 (chatlib.py+nlu.py, byte-matched, markers verified, restarted). Synced nlu.py to staging + pushed (origin now matches box). Cleaned on-box diag/probe scripts.

## 2026-09-14 — Logo adopted (favicon + brand mark)
- Owner picked the final EverList logo (own edit of the #20 circuit-trace mark: green E + cyan L, flat, dark tile).
- Archived master: docs/logo-prototypes/keepers/FINAL-EverlistLogo.png (1409x1409 RGBA; KEEP-20-flat-favicon.png as the glow-free variant).
- Built favicon assets from the owner file (resize only): static/favicon.svg (512px PNG embedded in SVG, keeps the /favicon.svg contract) + static/logo-512.png.
- Synced to .staging-repo/everlist/static/ (md5-identical). No app code touched; index.html keeps referencing /favicon.svg.
- Verified: SVG parses, embedded PNG decodes at 512, hub/staging md5 match. Full make test not run (asset-only swap).
- Full exploration archive: docs/logo-prototypes/ (README.md index; 15+ dirs of AI-generated masters, all preserved).

## 2026-09-15 — Original palette restored + new logo live (deploy 3da8987)
- Owner: "i liked the original colors more. change them back." → reverted `static/style.css` tokens to the pre-redesign palette (#0d1117 bg, #4ade80/#22c55e green, original bubbles); kept the approved Direction A layout (centered dock + Discover grid). Plum/blue tag accents folded back into the green.
- New logo: owner-uploaded EverlistLogo.png (archived as `docs/logo-prototypes/keepers/FINAL-EverlistLogo.png`, 1409² RGBA). Cropped to the mark (208,205,1250,1247) → `static/logo-512.png` + `favicon.svg` = embedded PNG per the established Caddy contract (`@chat` routes `/favicon.svg` only; header `<img>` points there — no Caddy change). Crop visually verified (mark intact, nothing clipped).
- Gate anomaly: first `make test` run DIED silently mid-M9 — no traceback, log just stops. Relaunched detached (nohup) → **ALL PASSED** (717 PASS lines, M8 chain E2E 17/17 live sim, M9 payout, A2A booking escrow=HELD). Lesson: truncated log + missing process = inconclusive; rerun, never claim green from a partial log. The killed gate also took the 8812/8814 dev stack down; `render_up.sh` restored it.
- Deploy (key-only SSH, `~/.ssh/everlist_deploy`): pushed 666156b..3da8987. On-box pull aborted twice: (1) git `safe.directory` (root vs deploy-owned tree), (2) 8 stale hot-patched files blocking merge. Verified BEFORE discarding: 6/8 byte-identical to origin/main (redundant), the 2 that differed were exactly style.css + favicon.svg — superseded by this deploy, which was the point of it. checkout + ff-pull → on-box 3da8987, restarted everlist-webchat, 4 units active.
- LIVE verified: title, style.css markers (#0d1117 ×2, #4ade80 ×4, zero dark-gray/sage hex), favicon serves the 512² logo, `/api/listings` 200. everlist.network = original colors + new logo.
- Lesson: `systemctl is-active: active` is NOT deploy proof — the first restart ran against the OLD tree because the pull had aborted. Only on-box `git log` + live byte markers prove a deploy.

## 2026-09-15 — Web buildout plan drafted (owner review pending)
- Owner asked to "build the site out fully" with a detailed plan first. Grounded in strategy-boundaries.md (I1–I9 invariants), backlog-v3, SPEC.md, master plan; audited hub endpoint surface (39 routes) vs web UI (grid+chat slice).
- Wrote docs/web-buildout-plan.md: 7 phases (W0 hygiene → W5 growth, W6/W7 gated), architecture decisions D1–D9 (hub stays pure JSON per SPEC §1; webchat becomes human-side server with tiny SSR for /l/{id} SEO; vanilla JS no build step; login-only UI auth in W1), full feature map across visitor/account/organizer/trust/chat/agents/ops, route map for Caddy expansion, risks, 7 open questions.
- Nothing implemented; awaiting owner review.

## 2026-09-15 — C9g: the seam gets a voice (owner: "thin and then rounded we try")
- Owner iterated on the seam between entries: no ornaments on the line ("too distracting"), the line itself may vary, and the ends are free to change too (after C9f the ╟╢ connectors pointed at nothing).
- Explored thread weights (thin/heavy/double/dashed/stipple/dotted) x end caps (bare/rounded/square/double/stub) as text-only options; owner picked thin ─ + rounded ╰╯.
- Applied: seam is now ╰─...─╯ (59 cells, machine-verified). Knot rules (╔═፨/╠/╚) unchanged — hierarchy holds: heavy framed open/close, soft rounded seams between entries.
- INFRA fix found live: the container was recreated overnight and `make up` launched the hub with $(PYTHON)=/opt/venv/bin/python, which lacks `cryptography` (requirements.txt pins it; the project venv has it). Hub now launches with $(VPY)=venv/bin/python like the wrapper. CI unaffected (test gate uses its own harness and overrides PYTHON explicitly).
- Full make test gate: ALL PASSED (GATE_EXIT=0). Live stack restarted (hub+wrapper+webchat); real browser E2E on :8804 — search renders the thin+rounded seam, numbered bold entries, $0/♟ counts, tail 'book <n>'; screenshot-verified.

## 2026-09-15 - C9h: the brand goes bold (owner: "Everlist also bold always...")
- C9g seam (thin + rounded ends) kept after owner review: "yes we keep it like this for now!"
- Brand wordmark in the sheet header now uses the same math-bold treatment as names/dates: the header reads "ARK EverList . N found" with EverList rendered in math-bold letters (one-line change: _mb("EverList") in the _smart_search header). Prose/help/errors and the CSS-styled website untouched.
- Gate note: /opt/venv (task runtime) lost cryptography/httpx mid-day; the Makefile documents PYTHON as overridable, so the gate ran with PYTHON=venv/bin/python (project venv pins everything in requirements.txt). ALL PASSED.
- Live E2E on :8804 (fresh session, search jazz berlin): DOM-verified bold brand header, bold names/dates, thin rounded seams, numbered entries, $/pawn/escrow/quote/link rows, tail "book <n>". Screenshot + page-content verified.
- DOC LESSON (process): appending an entry containing raw math-bold glyphs through a shell heredoc corrupted them into lone surrogates and the open('w') truncate-on-crash emptied worklog.md (committed empty in b1f85ed). Restored here from git HEAD~1. Rule: never embed math-bold literals in heredocs/scripts - describe them or use runtime _mb().
- Observation (not chased, out of scope): the Discover side panel shows "hub offline" while the chat search works - likely the panel's browser-side fetch (CORS) vs the chat's server-side proxy; pre-existing, worth a look on the VPS pass.
