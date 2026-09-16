# EverList — Strategy Boundaries (owner-pinned)

Pinned: 2026-09-12. These are durable strategic decisions agreed with the owner.
Do not relitigate them in normal work; change only with an explicit owner call.

## 1. What EverList is — the listing litmus test

> **A listing = something with a date where a human is needed or wanted.**

- Offers AND seeks ("offers yoga class" / "kitchen help wanted"), both first-class roles.
- Passes: events, classes, jobs, services, rides, marketplace items with dates.
- Fails: "I am an agent that summarizes PDFs, hire me" — no date, no human needed. That is an Agentverse entry, not an EverList listing.
- **Agents booking human labor is a first-class use case** (owner-pinned 2026-09-13): an agent posting a seek for human work passes the litmus test — a human is needed. **Escrow is the gig-trust moat**: the worker sees money locked in the contract before starting, making non-payment structurally impossible. Agent posters carry chat provenance (M14), caps, and rate limits; the jobs vertical keeps this north star.

## 2. FET boundary — no agents-only list, ever

- **Never build a directory of agents.** Agents are the readers, actors, and payers — not entries.
- FET/Agentverse = agent↔agent infrastructure and marketplace (their layer). EverList = world↔agent content with a real economy (our layer). Complementary today; Agentverse is a distribution channel (our booking agent lives there).
- Competition posture: FET could compete on paper (treasury, 2.7M-agent distribution) but listings are a marketplace-ops business (organizer supply, moderation, schema curation) that does not match their DNA. Watch, don't fear. Our real competitor is an **empty hub**.
- Our moat: listing-first marketplace ops + open protocol + community-defined schemas + chain-agnostic core.

## 3. Token policy — no own cryptocurrency

- **No EverList coin. Now, and probably never for payments.**
- Money = stablecoins (USDC etc.) + FET where ecosystem-native. Trust = escrow and deposits (skin in the game), not a token. Ownership = open protocol + open registry, not a coin.
- Revisit ONLY when all three hold: (1) real transaction volume post-pilot, (2) a function no existing asset serves — the honest candidate is **governance** (fee params, schema admission, hub policy), not payments, (3) legal review done (EU/MiCA).
- Rationale: regulation lift, trust poisoning of the fair-marketplace positioning, focus. Token value follows usage; it does not create it.

## 4. Identity — permanent two-tier model

- **Tier 1: effortless signup, forever** (Ed25519 keypair / account code). The default door for everyone.
- **Tier 2: Midnight ZK personhood, optional** — adds trust benefits (one human = one account, verified badge, merchant gating lever).
- Custom cryptographic auth expansion is FROZEN; prefer established paths per owner directive.

## 5. Payment & escrow policy

Owner-pinned payment terms (rails, refund windows, deposits, mutual agreement semantics): **SPEC.md §19** in the experiment tree — the single source of truth. Summary: terms advertised on the listing, booking = agreement, changes post-booking need both parties; escrow is the default rail whenever real money attaches, x402 instant is merchant opt-in; deposits are the standard merchant lever; Tier-2 buyer gating is a premium flag (deferred until Tier-2 is live on the server).

## 6. The three hard problems — standing answers

| Problem | Answer |
| --- | --- |
| Convincing organizers to post | One-message chat listing (proven live), accounts + recovery, free forever. The remaining half is human outreach (P1) — no code replaces it |
| Keeping scammers out | Escrow economics (cost to scam), Tier-2 identity, rate limits + PoW, three red-team passes, immutable reputation. Refund-window + auto-release policy per SPEC §19 |
| Keeping categories clean | Fail-closed community schemas (C5), `/suggest` for valid vocab, tags forgiving via FTS5. Curation at scale = governance, grown into later |

## 7. Sequencing

VPS launch → Agentverse mailbox on the server → P1 pilot (two organizers, ≥3 bookings) → go/no-go. Product code is complete; the bottleneck is deliberately human work. Gated items (real money, MCP, LLM chat, SQLite) stay behind their triggers.

## 8. Federation posture — curated partner network (owner call, 2026-09-16)

Refines the "open protocol + open registry" wording in §1/§3. This section wins where they differ.

1. **Listed = partner.** Appearing in the EverList network registry requires partnership;
   verified status is granted and revoked solely by the owner. No self-service onboarding, ever.
2. **The software stays open.** Anyone may run the hub tech unaffiliated — that cannot and
   should not be prevented (MIT). Unaffiliated hubs get no trust rails (no verified tier, no
   signature, no aggregator inclusion, no endorsement). The separator is a TRUST GRADIENT,
   not two webs: same protocol, one signed trusted index.
3. **Strategic upside of the open layer**: platform-risk insurance (partners trust joining
   because the tech isn't a trap) + a sales pipeline (successful unaffiliated operators are
   partnership prospects).
4. **Watch point**: brand hygiene. If unaffiliated hubs become associated with scams, the
   lever is making the trust gradient louder (signed registry, conformance checks, tier
   labels agents can verify), never attempting to ban the software.
5. **The partner scenario is the north star** (owner's Eventbrite example): a big operator
   brings its customers onto its own hub, and EverList aggregates all verified hubs. EverList
   profits via its own hub's fees now; protocol fee + trust-rails-as-a-service + being the
   aggregator in the partner era.
6. **Building paused.** Registry stays a prototype until a real partner conversation exists.
   Focus: organizer recruitment on the owner's hub + the web buildout plan.
