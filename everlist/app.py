"""agent-hub-v2 - open, fair agent commerce hub (stdlib only, MIT).

Universal booking core: any vertical (events, food, ...) is just a schema.
Fairness is enforced in the protocol:
- hub fee is CAPPED and declared in the manifest (violators are non-conformant)
- every transaction goes to a public ledger (GET /ledger)
- escrow by default: money is held until fulfillment is confirmed
- open participation: no auth gate on the protocol level (identity/staking is a pluggable layer)
"""
import json, os, sys, threading, time, hmac, hashlib, secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hublib  # I2/G5: versioned, domain-separated HMAC action tokens

# fee is fully hub-declared; no protocol cap. Agents judge via the public ledger.

def anon_ref():
    """I5: per-booking random opaque reference. NOT derivable from the name,
    NOT linkable across bookings by the same participant."""
    return "anon-" + secrets.token_hex(8)

# ---- seed data: two verticals, same core ---------------------------------
LISTINGS = [
    {"id": "evt-1", "vertical": "events", "title": "AI Builders Meetup",
     "category": "meetup", "tags": ["ai", "tech", "networking"],
     "date": "2026-09-15", "location": "Berlin", "price": 10.0,
     "capacity": 50, "registered": 0, "owner": "event-owner-agent"},
    {"id": "evt-2", "vertical": "events", "title": "Rooftop Jazz Night",
     "category": "concert", "tags": ["music", "jazz", "rooftop"],
     "date": "2026-09-12", "location": "Berlin", "price": 15.0,
     "capacity": 30, "registered": 0, "owner": "event-owner-agent"},
    {"id": "food-1", "vertical": "food", "title": "Margherita Pizza",
     "category": "pizzeria", "tags": ["pizza", "italian", "vegetarian"],
     "merchant": "Luigi's", "preparation_minutes": 20, "price": 8.5,
     "available": True, "owner": "lufgis-pizzeria-agent"},
    {"id": "food-2", "vertical": "food", "title": "Vegan Bowl",
     "category": "vegan", "tags": ["vegan", "healthy", "salad"],
     "merchant": "Green Corner", "preparation_minutes": 15, "price": 11.0,
     "available": True, "owner": "green-corner-agent"},
]
BOOKINGS = []   # booking records (pseudonymous)
SECRETS = {}    # booking id -> secret (private detail access)
LEDGER = []     # public, append-only transaction ledger
IDEMPOTENCY = {}  # I4: Idempotency-Key -> {hash, response} (persisted with G1)
RATE_LIMITS = {}  # G4: principal -> [timestamps] for the 60s window
# B3c-accounts: chat-native organizer accounts (pilot-grade auth).
# acct-<id> -> {code_hash, bound: [agent names], human_verified, verified_by, created}.
# The account CODE is the credential (shown ONCE, sha256-only); login mints tokens
# with sub=acct-<id>; open /access can NEVER mint acct- principals (forgery wall).
ACCOUNTS = {}
ACCOUNTS_CAP = 10_000  # hard cap: unbounded signups = state-bloat DoS
# auth endpoints: global fixed-window limits (pilot-grade anti-bruteforce/DoS;
# per-IP is unreliable behind relays, documented honestly in SPEC)
AUTH_LIMITS = {"signup": (10, 3600), "login": (120, 60), "rotate": (10, 3600)}
AUTH_HITS = {k: [] for k in AUTH_LIMITS}


def _auth_allow(kind):
    """Fixed-window limiter for auth endpoints. Caller MUST hold LOCK."""
    limit, window = AUTH_LIMITS[kind]
    now = time.time()
    AUTH_HITS[kind] = [t for t in AUTH_HITS[kind] if now - t < window]
    if len(AUTH_HITS[kind]) >= limit:
        return False
    AUTH_HITS[kind].append(now)
    return True


LOCK = threading.Lock()

# G1: JSON snapshot persistence (atomic write on every mutation, load on start).
# Privacy: SECRETS (real attendee/buyer names) are NEVER persisted - identities stay
# memory-only by design; booking records + ledger + listings + idempotency + token
# nonces survive restarts.


def _persist_locked():
    """Atomic snapshot write. Caller MUST hold LOCK."""
    snap = {"listings": LISTINGS, "bookings": BOOKINGS, "ledger": LEDGER,
            "idempotency": IDEMPOTENCY, "accounts": ACCOUNTS,
            "nonces": {n: e for n, e in getattr(hublib, "_NONCES", {}).items()},
            "x402_nonces": (x402verify.snapshot_used_nonces() if PAY_MODE == "testnet" else {}),
            "settlements": (SETTLEMENTS.snapshot() if PAY_MODE == "testnet" else {})}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
    os.replace(tmp, STATE_FILE)  # atomic on POSIX


def _load_state():
    """Load snapshot at startup. Missing/corrupt file = fresh start (fail-open dev)."""
    if not os.path.exists(STATE_FILE):
        return
    try:
        with open(STATE_FILE) as f:
            snap = json.load(f)
        LISTINGS[:] = snap.get("listings", [])
        BOOKINGS[:] = snap.get("bookings", [])
        LEDGER[:] = snap.get("ledger", [])
        IDEMPOTENCY.update(snap.get("idempotency", {}))
        ACCOUNTS.update(snap.get("accounts", {}))
        hublib._NONCES.update(snap.get("nonces", {}))
        if PAY_MODE == "testnet":
            x402verify.load_used_nonces(snap.get("x402_nonces", {}))
            SETTLEMENTS.load(snap.get("settlements", {}))
        print(f"G1: restored {len(BOOKINGS)} bookings, {len(LEDGER)} ledger entries, "
              f"{len(LISTINGS)} listings from {STATE_FILE}")
    except Exception as ex:
        print(f"G1 WARNING: could not load state ({ex}); starting fresh")

# I2/H1: signing keys from env; never log or return these
# G5: fail-closed in production — a missing key must never silently fall back to dev defaults
BOOKING_KEY = os.environ.get("HUB_BOOKING_KEY", "dev-booking-key-change-me")
ADMIN_KEY = os.environ.get("HUB_ADMIN_KEY", "dev-admin-key-change-me")
RUN_ENV = os.environ.get("HUB_ENV", "development")
if RUN_ENV == "production" and (BOOKING_KEY.startswith("dev-") or ADMIN_KEY.startswith("dev-")):
    sys.stderr.write("FATAL: HUB_ENV=production requires HUB_BOOKING_KEY and HUB_ADMIN_KEY (no dev defaults)\n")
    sys.exit(78)
# G3: all operational config via env with sane defaults
PORT = int(os.environ.get("HUB_PORT", "8802"))
FEE_PCT = float(os.environ.get("HUB_FEE_PCT", "1.0"))
if not (0 <= FEE_PCT <= 50):
    sys.stderr.write(f"FATAL: HUB_FEE_PCT must be in [0, 50], got {FEE_PCT}\n")
    sys.exit(78)
DATA_DIR = os.environ.get("HUB_DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.environ.get("HUB_STATE_FILE", os.path.join(DATA_DIR, "state.json"))
PAY_MODE = os.environ.get("HUB_PAY_MODE", "simulated")  # simulated | testnet (real EIP-3009 verification)
if PAY_MODE not in ("simulated", "testnet"):
    sys.stderr.write(f"FATAL: HUB_PAY_MODE must be simulated|testnet, got {PAY_MODE}\n")
    sys.exit(78)
if PAY_MODE == "testnet":
    import x402verify  # real EIP-3009 verification (C3a); settlement = C3b
    import x402facilitate
SETTLE_MODE = os.environ.get("HUB_SETTLE_MODE", "off")  # off (verify-only) | auto (settle after verify)
if SETTLE_MODE not in ("off", "auto"):
    sys.stderr.write(f"FATAL: HUB_SETTLE_MODE must be off|auto, got {SETTLE_MODE}\n")
    sys.exit(78)
if PAY_MODE == "testnet":
    FACIL = x402facilitate.FacilitatorClient(
        os.environ.get("HUB_FACILITATOR_URL", x402facilitate.DEFAULT_FACILITATOR),
        os.environ.get("HUB_FACILITATOR_KEY"), timeout=15)
    SETTLEMENTS = x402facilitate.SettlementRegistry()
RATE_BOOKS_PER_MIN = int(os.environ.get("HUB_RATE_BOOKS_PER_MIN", "10"))

# C2: x402 premium endpoint config (wire format per payments_x402.md)
import base64
PREMIUM_PRICE_USD = float(os.environ.get("HUB_PREMIUM_PRICE_USD", "0.01"))
PAYTO = os.environ.get("HUB_PAYTO", "0x1111111111111111111111111111111111111111")  # SIMULATED default
BASE_SEPOLIA_USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # official testnet USDC
# G5: identities stay memory-only by default (no persistence of secrets/PII);

VERTICAL_SCHEMAS = {
    "events": {"required": ["title", "date", "location", "price", "capacity"],
               "optional": ["description", "category", "tags", "url"],
               "categories": ["meetup", "concert", "workshop", "conference", "market",
                               "sports", "community", "party", "exhibition", "other"],
               "booking": {"required": ["attendee"], "action": "register+pay"}},
    "food":   {"required": ["title", "merchant", "price"],
               "optional": ["description", "category", "tags", "url", "preparation_minutes"],
               "categories": ["pizzeria", "vegan", "asian", "burger", "bakery",
                               "cafe", "grocery", "other"],
               "booking": {"required": ["buyer", "quantity"], "action": "order+pay"}},
}

# I1: field ownership. Server-owned fields may never come from clients.
RESERVED_BOOKING_FIELDS = {"id", "vertical", "escrow", "amount", "hub_fee",
    "owner_payout", "created", "booking_secret", "rail", "confirmation"}

# EverList taxonomy: category = controlled vocab per vertical (validated at
# listing time); tags = free-form, normalized (lowercase, trimmed, deduped,
# capped). Both are optional; search is faceted over vertical+category+tags.
TAXONOMY = {"tag_max": 8, "tag_len": 24}


def normalize_tags(raw):
    """Free-form tags -> clean list. Accepts list or "a, b" string."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = raw.replace(";", ",").split(",")
    if not isinstance(raw, list):
        raise ValueError("tags must be a list or comma-separated string")
    tags = []
    for t in raw[: TAXONOMY["tag_max"] * 3]:  # input cap before dedupe
        t = str(t).strip().lower()[: TAXONOMY["tag_len"]]
        if t and t not in tags:
            tags.append(t)
        if len(tags) >= TAXONOMY["tag_max"]:
            break
    return tags
CLIENT_BOOKING_FIELDS = {
    "events": {"attendee", "quantity"},
    "food": {"buyer", "quantity"},
}
RESERVED_LISTING_FIELDS = {"id", "registered", "available", "owner", "manage_code_hash"}  # owner = authenticated principal; manage_code_hash = server-only (anti-spoof)

def hub_fee_c(price_c):
    """Hub-declared fee (G3: HUB_FEE_PCT env, default 1%). Integer minor units internally."""
    return (price_c * int(round(FEE_PCT * 100))) // 10000


def hub_fee(price):
    """Fair fee on major units (edge adapter over integer internals)."""
    return hub_fee_c(int(round(price * 100))) / 100.0

class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data, extra_headers=None):
        body = json.dumps(data, indent=2).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/.well-known/agent-hub.json":
            # standard discovery location - agents probe any domain for this
            return self._json(200, {
                "protocol": "agent-hub/0.2", "open_source": "MIT",
                "hub": "agent-hub-v2",
                "description": "Open, community-driven commerce hub for AI agents. Universal booking core, per-vertical schemas.",
                "fairness": {"fee_policy": {"actual_fee_pct": FEE_PCT,
                                "note": "fully declared by hub; no protocol cap. Agents verify declared vs ledger and choose"},
                              "ledger": "/ledger", "escrow": True,
                              "open_registry": "/registry"},
                "identity": {"booking_requires": "verified-human credential (ZK: real human, private by default)",
                              "adapters": [
                                  {"scheme": "midnight-zk-personhood", "status": "flagship (ZK: real human, identity stays private)"},
                                  {"scheme": "email-or-phone-attestation", "status": "interim stub until Midnight contract is live"}],
                              "disputes": "selective disclosure to auditors via ZK (Midnight)"},
                "payments": {
                    "protocol": "x402",
                    "pricing": {"unit": "merchant fiat (EUR/USD) or USDC",
                                 "rule": "prices are NEVER denominated in volatile assets"},
                    "accepted_assets": [
                        {"asset": "USDC (EVM)", "role": "native x402 'exact' scheme - verified per C1 research", "rail": "x402"},
                        {"asset": "ETH", "role": "x402 EVM chains - HYPOTHESIS until C3 verification", "rail": "x402"},
                        {"asset": "ADA", "role": "custom adapter required - x402 core does not cover Cardano (C1 finding)", "rail": "custom"},
                        {"asset": "FET", "role": "custom adapter; for ASI-native agents; merchants never receive FET (round-5)", "rail": "custom"}],
                    "deferred": [
                        {"asset": "BTC", "why": "needs processor/Lightning adapter - later"},
                        {"asset": "NIGHT/DUST", "why": "Midnight mainnet ~6mo old, no merchant off-ramp yet - revisit"}],
                    "conversion": "at payment time via DEX router/processor; hub never custodies funds",
                    "rails": {
                        "default": "x402 transparent (USDC/ETH/ADA/FET) - cheap, standard, merchant-friendly",
                        "privacy": "midnight shielded rail (Zswap): hides payer, amount, and asset; private escrow state via Compact; per-transaction selective disclosure for disputes/compliance",
                        "why_two": "public rails leak purchase history; sensitive bookings (medical, legal, personal) need shielded payment by default"},
                    "principle": "buyers pay in what they hold; merchants receive stable value; privacy is a choice"},
                "distribution": {"channels": ["agentverse marketplace (hub runs as uAgent)",
                                 "almanac registration (agent identity/address)",
                                 "agent framework skills (A0, MCP, uAgents wrapper)"],
                                  "role_of_fet": "ecosystem access + agent discovery; NOT a merchant settlement currency"},
                "fet_utility": {
                    "infrastructure": ["hub runs as uAgent", "almanac registration (agent identity, small FET)", "agentverse marketplace listing"],
                    "earns_fet": "premium hub services sold to ASI-native agents (paid in FET -> converted to USDC at settlement)",
                    "stakes_fet": "optional verified-hub trust bond in registry (slashed on proven fraud)",
                    "never": "merchant settlement currency"},
                "privacy": {"pseudonymous_ids": True,
                              "booking_details": "visible only with booking secret",
                              "note": "public ledger shows amounts + pseudonyms only"},
                "capabilities": {"search": "/search?q=", "verticals": "/verticals",
                                  "listings": "/listings", "book": "POST /book",
                                  "bookings": "/bookings", "orders": "/orders (merchant view, list token)",
                                  "ledger": "/ledger",
                                  "add_listing": "POST /listings",
                                  "premium": {"endpoint": "/premium/events", "protocol": "x402",
                                               "status": ("TESTNET - real EIP-3009 signature verification (eth-account), no on-chain settlement yet (C3b)" if PAY_MODE == "testnet" else "SIMULATED - stub verification, no real settlement (C3a = real EIP-3009 on base-sepolia)")}},
                "schemas": VERTICAL_SCHEMAS})
        if u.path == "/premium/events":
            """C2: x402-protected richer feed. SIMULATED mode: stub verification.
            Wire format per payments_x402.md (C1 research)."""
            pay_hdr = self.headers.get("X-PAYMENT", "")
            if not pay_hdr:
                terms = {"x402Version": 1, "error": "Payment Required",
                         "mode": PAY_MODE.upper(),
                         "accepts": [{
                             "scheme": "exact", "network": "base-sepolia",
                             "asset": BASE_SEPOLIA_USDC,
                             "maxAmountRequired": str(int(round(PREMIUM_PRICE_USD * 1_000_000))),
                             "payTo": PAYTO,
                             "resource": "http://localhost:8802/premium/events",
                             "description": "Premium events feed (agent-hub-v2)",
                             "maxTimeoutSeconds": 300,
                             "extra": {"name": "USD Coin", "version": "2"}}],
                         "note": ("TESTNET mode: real EIP-3009 signature verification; on-chain settlement via facilitator is C3b."
                                  if PAY_MODE == "testnet" else
                                  "SIMULATED payment verification - no real settlement. Production mode verifies EIP-3009 signatures (C3a).")}
                return self._json(402, terms)
            if PAY_MODE == "testnet":
                info, err = x402verify.verify_payment(
                    pay_hdr, network="base-sepolia", pay_to=PAYTO,
                    max_amount_units=int(round(PREMIUM_PRICE_USD * 1_000_000)))
                if err:
                    return self._json(402, {"x402Version": 1, "error": "invalid payment",
                                            "detail": err, "mode": "TESTNET"})
                with LOCK:
                    _persist_locked()  # C3a: persist used x402 nonce IMMEDIATELY (N7 replay-across-restart)
                    fp = x402facilitate.payment_fingerprint({"from": info["from"],
                                                              "to": PAYTO, "value": info["value"],
                                                              "nonce": info["nonce"]})
                    srec = None
                    if SETTLE_MODE == "auto":
                        # C3b: idempotent settlement; duplicate submissions return the
                        # recorded result without re-calling the facilitator.
                        # paymentPayload = the REAL decoded x402 payload (signed auth)
                        srec = SETTLEMENTS.settle(fp, info["payload"],
                                                   {"scheme": "exact", "network": "base-sepolia",
                                                    "asset": BASE_SEPOLIA_USDC,
                                                    "payTo": PAYTO,
                                                    "maxAmountRequired": str(info["value"])},
                                                   FACIL)
                        if srec["status"] == "settled":
                            LEDGER.append({"kind": "x402_settlement", "ts": time.time(),
                                            "detail": {"fingerprint": fp, "tx": srec["tx"],
                                                        "value": info["value"], "payer": info["from"],
                                                        "network": srec.get("network", "base-sepolia")}})
                        _persist_locked()  # settlement record + ledger event persisted together
                    res = [l for l in LISTINGS if l["vertical"] == "events"]
                    rich = [{**l, "premium_meta": {"owner_public": l.get("owner", ""),
                             "fill_ratio": round(l.get("registered", 0) / max(1, l.get("capacity", 1)), 3),
                             "payment": {"mode": "TESTNET", "verified": True,
                                          "settlement": (srec["status"] if srec else "pending (HUB_SETTLE_MODE=off)"),
                                          "fingerprint": x402verify.payment_fingerprint(info),
                                          "payer": info["from"], "value": info["value"]}}} for l in res]
                if srec is not None and srec["status"] == "settled":
                    settle_info = {"status": "SETTLED", "tx": srec["tx"],
                                    "network": srec.get("network", "base-sepolia")}
                elif srec is not None and srec["status"] == "unknown":
                    settle_info = {"status": "UNKNOWN",
                                    "note": "facilitator timeout/error - outcome unproven; check facilitator before retry"}
                elif srec is not None:
                    settle_info = {"status": "FAILED", "error": srec.get("error", "facilitator rejected")}
                else:
                    settle_info = {"status": "NOT_ATTEMPTED",
                                    "note": "HUB_SETTLE_MODE=off - verified only; set auto to settle via facilitator"}
                return self._json(200, {"mode": "TESTNET",
                    "payment": {"scheme": "exact", "network": "base-sepolia", "asset": "USDC",
                                 "value": info["value"], "verified": True,
                                 "settlement": settle_info},
                    "count": len(rich), "events": rich})
            # simulated mode: decode + validate authorization (stub signature)
            try:
                dec = json.loads(base64.b64decode(pay_hdr).decode())
                auth = dec["payload"]["authorization"]
                assert dec.get("x402Version") == 1 and dec.get("scheme") == "exact"
                assert auth["to"] == PAYTO
                assert int(auth["value"]) >= int(round(PREMIUM_PRICE_USD * 1_000_000))
                now = int(time.time())
                assert int(auth["validAfter"]) <= now < int(auth["validBefore"])
            except Exception as ex:
                return self._json(402, {"x402Version": 1, "error": "invalid payment",
                                        "detail": str(ex)[:120], "mode": "SIMULATED"})
            with LOCK:
                res = [l for l in LISTINGS if l["vertical"] == "events"]
                # 'richer': only premium gets per-listing owner contact + inventory ratio
                rich = [{**l, "premium_meta": {"owner_public": l.get("owner", ""),
                         "fill_ratio": round(l.get("registered", 0) / max(1, l.get("capacity", 1)), 3),
                         "payment": "SIMULATED x402 exact/base-sepolia USDC"}} for l in res]
            return self._json(200, {"mode": "SIMULATED",
                "payment": {"scheme": "exact", "network": "base-sepolia", "asset": "USDC",
                             "value": auth["value"], "settlement": "stubbed (C3a = real EIP-3009 verification)"},
                "count": len(rich), "events": rich})
        if u.path == "/manifest.json":
            return self._json(200, {"hub": "agent-hub-v2", "version": "0.3",
                "type": "universal-commerce",
                "canonical_manifest": "/.well-known/agent-hub.json"})
        if u.path == "/verticals":
            return self._json(200, {"verticals": VERTICAL_SCHEMAS})
        if u.path == "/listings":
            v = parse_qs(u.query).get("vertical", [""])[0]
            with LOCK:
                res = [l for l in LISTINGS if not v or l["vertical"] == v]
            return self._json(200, {"count": len(res), "listings": res})
        if u.path == "/search":
            """Faceted search: q (substring) + structured filters.
            All filters AND-combined; agents can discover vocab via /verticals."""
            q = parse_qs(u.query)
            term = q.get("q", [""])[0].lower()
            fvert = q.get("vertical", [""])[0]
            fcat = q.get("category", [""])[0].strip().lower()
            ftag = q.get("tag", [""])[0].strip().lower()
            floc = q.get("location", [""])[0].strip().lower()
            fmax = q.get("max_price", [""])[0]
            with LOCK:
                res = [l for l in LISTINGS
                       if (not term or term in json.dumps(l).lower())
                       and (not fvert or l["vertical"] == fvert)
                       and (not fcat or l.get("category") == fcat)
                       and (not ftag or ftag in (l.get("tags") or []))
                       and (not floc or floc in str(l.get("location", "")).lower())]
                if fmax:
                    try:
                        lim = float(fmax)
                        res = [l for l in res if float(l.get("price", 0)) <= lim]
                    except ValueError:
                        return self._json(400, {"error": "max_price must be a number"})
            return self._json(200, {"count": len(res), "filters": {
                "q": term, "vertical": fvert, "category": fcat, "tag": ftag,
                "location": floc, "max_price": fmax or None}, "listings": res})
        if u.path == "/bookings":
            cred = self.headers.get("X-Hub-Token", "")  # I2: token required, principal-scoped
            payload, err = None, "missing X-Hub-Token"
            if cred:
                for act in ("book", "list"):
                    payload, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                    if payload: break
            if not payload:
                return self._json(401, {"error": err or "invalid token",
                    "hint": "POST /access {agent: '<your-agent>'} then send token as X-Hub-Token"})
            me = payload["sub"]
            with LOCK:
                mine = [b for b in BOOKINGS if b.get("booked_by") == me]
            return self._json(200, {"bookings": mine, "principal": me,
                "note": "principal-scoped: you see only your own bookings"})
        if u.path == "/orders":
            """Merchant-scoped incoming orders (E1/E2 gap fix): bookings made
            against listings this principal owns. Privacy projection applies:
            pseudonymous refs only — real buyer names stay in the secret store."""
            cred = self.headers.get("X-Hub-Token", "")
            payload, err = None, "missing X-Hub-Token"
            if cred:
                payload, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False)
            if not payload:
                return self._json(401, {"error": err or "invalid token",
                    "hint": "merchant list token required (POST /access acts=['list'])"})
            me = payload["sub"]
            with LOCK:
                my_listings = {l["id"] for l in LISTINGS if l.get("owner") == me}
                pubfields = ("id", "listing_id", "vertical", "escrow", "amount",
                             "hub_fee", "owner_payout", "quantity", "created",
                             "booked_by", "attendee", "buyer")
                orders = [{k: b[k] for k in pubfields if k in b}
                          for b in BOOKINGS if b.get("listing_id") in my_listings]
            return self._json(200, {"orders": orders, "merchant": me, "count": len(orders)})
        if u.path == "/ledger":
            with LOCK:
                # totals cover booking-fee entries; x402 settlement events carry
                # their own shape (kind=x402_settlement, detail.*) and are excluded
                totals = {"total_volume": round(sum(t.get("amount", 0) for t in LEDGER), 2),
                          "total_hub_fees": round(sum(t.get("hub_fee", 0) for t in LEDGER), 2),
                          "x402_settlements": sum(1 for t in LEDGER if t.get("kind") == "x402_settlement")}
            return self._json(200, {"ledger": LEDGER, "totals": totals,
                "note": "public, append-only; amounts + per-booking random opaque refs only - participant identities are never stored. Residual-linkage disclosure: a single hub's operator could still correlate bookings within its own private store; true cross-hub unlinkability requires the Midnight personhood adapter (A2)",
                "privacy": "anon refs are random per booking - NOT name-derived, NOT linkable across bookings"})
        if u.path == "/challenge":
            """D2: registry ownership proof - echo the nonce to prove URL control."""
            q = parse_qs(u.query)
            nonce = (q.get("nonce") or [""])[0]
            return self._json(200, {"challenge_response": nonce,
                                     "note": "registry ownership proof (D2)"})
        if u.path == "/registry":
            return self._json(200, {"tiers": {
                    "open": "unverified self-registration; agents browse at own risk",
                    "verified": "community-reviewed hubs meeting content-policy + fairness checks"},
                "responsibility": "each hub operator is responsible for the legality of its own listings; agents and registries filter",
                "hubs": [
                    {"url": "http://localhost:8802", "type": "universal-commerce", "tier": "verified",
                     "content_policy": "/policy"}]})
        if u.path.startswith("/book/"):
            parts = [p for p in u.path.split("/") if p]
            if len(parts) == 2:  # GET /book/{id} - private details
                bid = parts[1]
                cred = self.headers.get("X-Hub-Token", "")  # H1: credentials in headers, never in URLs
                rec = SECRETS.get(bid)
                if not rec: return self._json(404, {"error": "no booking"})
                if not hmac.compare_digest(cred, rec["secret"]):
                    p, err = hublib.verify_token(BOOKING_KEY, cred, "details",
                                                 single_use=False, subject=bid)
                    if not p or p.get("sub") != bid:
                        return self._json(403, {"error": err or "invalid or missing credential"})
                with LOCK:
                    b = next((x for x in BOOKINGS if x["id"] == bid), None)
                if not b: return self._json(404, {"error": "no booking"})
                return self._json(200, {"id": bid, "private_details": {**b, **rec["private"]}})
        return self._json(404, {"error": "not found", "hint": "GET /.well-known/agent-hub.json"})

    def do_POST(self):
        path = urlparse(self.path).path
        # G4: global request-body cap (64KB) - reject before parsing
        if int(self.headers.get("Content-Length", 0)) > 65536:
            return self._json(413, {"error": "request body too large (max 64KB)"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            data = json.loads(raw) if raw.strip() else {}
        except Exception as ex:
            return self._json(400, {"error": str(ex)})
        if path == "/access":
            # I2 interim bootstrap: open issuance PRE-personhood (documented; replaced by Midnight A2)
            agent = str(data.get("agent", "")).strip()
            if not agent or len(agent) > 64:
                return self._json(400, {"error": "agent name required (max 64 chars)"})
            if agent.lower().startswith("acct-"):
                # B3c-accounts forgery wall (CASE-INSENSITIVE: ACCT- would mint a token
                # whose sub only differs by case — future case-insensitive comparisons
                # would turn that into account takeover; refuse the whole namespace)
                return self._json(403, {"error": "acct- principals require the account code: POST /accounts/login {account_code, agent}"})
            acts = data.get("acts", ["book"])
            if not isinstance(acts, list) or not acts or any(a not in ("book", "list") for a in acts):
                return self._json(400, {"error": "acts must be a non-empty subset of ['book', 'list']"})
            ttl = min(max(int(data.get("ttl", 3600)), 60), 24*3600)
            toks = {a: hublib.mint_token(BOOKING_KEY, a, agent, ttl=ttl) for a in acts}
            return self._json(201, {"agent": agent, "tokens": toks, "ttl": ttl,
                "note": "interim open bootstrap (pre-personhood): anyone may obtain book/list tokens today; production identity = Midnight zk-personhood (A2)",
                "usage": "send as X-Hub-Token header on POST /book, POST /listings, GET /bookings"})
        if path == "/accounts/signup":
            # B3c-accounts: chat-native organizer account. The CODE is the credential.
            agent = str(data.get("agent", "")).strip()
            if not agent or len(agent) > 64 or agent.lower().startswith("acct-"):
                return self._json(400, {"error": "agent name required (max 64 chars, no acct- prefix)"})
            with LOCK:
                if len(ACCOUNTS) >= ACCOUNTS_CAP:
                    return self._json(429, {"error": "hub at account capacity; contact the operator"})
                if not _auth_allow("signup"):
                    return self._json(429, {"error": "signup rate limit reached, retry later"})
                aid = "acct-" + secrets.token_hex(4)
                while aid in ACCOUNTS:
                    aid = "acct-" + secrets.token_hex(4)
                code = "acct-" + secrets.token_hex(8)
                ACCOUNTS[aid] = {"code_hash": hashlib.sha256(code.encode()).hexdigest(),
                                 "bound": [agent], "human_verified": False,
                                 "verified_by": None, "created": time.time()}
                _persist_locked()
            return self._json(201, {"account_id": aid, "account_code": code,
                "human_verified": False,
                "code_note": "shown ONCE — this code OWNS the account; store it like a seed phrase",
                "next": "verify as human: hub operator vouch (pilot) or POST /accounts/verify email code (deploy); login from any chat: POST /accounts/login {account_code, agent}"})
        if path == "/accounts/login":
            code = str(data.get("account_code", "")).strip()
            agent = str(data.get("agent", "")).strip()
            if not code or not agent or len(agent) > 64 or agent.lower().startswith("acct-"):
                return self._json(400, {"error": "account_code + agent required"})
            ch = hashlib.sha256(code.encode()).hexdigest()
            with LOCK:
                if not _auth_allow("login"):
                    return self._json(429, {"error": "login rate limit reached, retry later"})
                aid = next((a for a, v in ACCOUNTS.items() if hmac.compare_digest(v["code_hash"], ch)), None)
                if not aid:
                    return self._json(403, {"error": "invalid account code"})
                if agent not in ACCOUNTS[aid]["bound"]:
                    if len(ACCOUNTS[aid]["bound"]) >= 25:  # binding cap: no unbounded list growth
                        return self._json(409, {"error": "agent binding limit (25) reached for this account"})
                    ACCOUNTS[aid]["bound"].append(agent)  # multi-agent binding (agent-led onboarding)
                _persist_locked()
            acts = ["book", "list"]
            toks = {a: hublib.mint_token(BOOKING_KEY, a, aid, ttl=24*3600) for a in acts}
            return self._json(200, {"account_id": aid, "agent": agent,
                "human_verified": ACCOUNTS[aid]["human_verified"],
                "tokens": toks, "ttl": 24*3600,
                "note": "tokens act AS the account (sub=acct-<id>) for 24h; edit/delete need no per-listing codes"})
        if path == "/accounts/vouch":
            # Pilot-grade human proof: hub operator vouches by name (admin-key gated).
            # Production: email 6-digit (deploy) or Midnight zk-personhood (A2).
            admin = self.headers.get("X-Admin-Key", "")
            if not admin or not hmac.compare_digest(admin, ADMIN_KEY):
                return self._json(403, {"error": "admin key required (X-Admin-Key)"})
            aid = str(data.get("account_id", "")).strip()
            with LOCK:
                if aid not in ACCOUNTS:
                    return self._json(404, {"error": f"no account {aid}"})
                ACCOUNTS[aid]["human_verified"] = True
                ACCOUNTS[aid]["verified_by"] = "admin-vouch"
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid,
                "human_verified": True, "verified_by": "admin-vouch"})
        if path == "/accounts/rotate":
            # recovery: a leaked code must never mean a lost account. Current code
            # proves control -> mints a NEW code, invalidates the old (hash swap).
            # Already-issued 24h login tokens stay valid until expiry (documented).
            code = str(data.get("account_code", "")).strip()
            if not code:
                return self._json(400, {"error": "account_code required"})
            ch = hashlib.sha256(code.encode()).hexdigest()
            with LOCK:
                if not _auth_allow("rotate"):
                    return self._json(429, {"error": "rotate rate limit reached, retry later"})
                aid = next((a for a, v in ACCOUNTS.items() if hmac.compare_digest(v["code_hash"], ch)), None)
                if not aid:
                    return self._json(403, {"error": "invalid account code"})
                new_code = "acct-" + secrets.token_hex(8)
                ACCOUNTS[aid]["code_hash"] = hashlib.sha256(new_code.encode()).hexdigest()
                ACCOUNTS[aid]["rotated"] = time.time()
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid, "account_code": new_code,
                "code_note": "OLD CODE IS NOW INVALID — new code shown ONCE, store it; existing 24h login tokens remain valid until expiry"})
        if path == "/listings":
            cred = self.headers.get("X-Hub-Token", "")  # I2: list token required
            p, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False) if cred \
                else (None, "missing X-Hub-Token")
            if not p:
                return self._json(401, {"error": err or "invalid list token",
                    "hint": "POST /access {agent: '<your-agent>', acts: ['list']} then send token as X-Hub-Token"})
            v = data.get("vertical")
            if v not in VERTICAL_SCHEMAS:
                return self._json(400, {"error": f"unknown vertical: {v}",
                    "known": list(VERTICAL_SCHEMAS)})
            schema = VERTICAL_SCHEMAS[v]
            missing = [f for f in schema["required"] if f not in data]
            if missing: return self._json(400, {"error": f"missing fields: {missing}"})
            # I1: reject reserved fields, validate types, strip unknowns
            reserved = [k for k in data if k in RESERVED_LISTING_FIELDS and k != "available"]
            if reserved: return self._json(400, {"error": f"reserved fields: {reserved}"})
            try:
                price = float(data["price"])
                if not (price >= 0 and price == price and price not in (float("inf"), float("-inf"))):
                    raise ValueError("price must be a nonnegative finite number")
                data["price"] = price
                if v == "events":
                    cap = int(data["capacity"])
                    if cap <= 0: raise ValueError("capacity must be positive")
                    data["capacity"] = cap
                    if not str(data.get("date", "")).strip():
                        return self._json(400, {"error": "date required for events"})
            except (TypeError, ValueError) as ex:
                return self._json(400, {"error": f"invalid field: {ex}"})
            # EverList taxonomy: category from controlled vocab (optional but
            # must be valid if given); tags free-form but normalized+bounded.
            if "category" in data and data["category"] is not None:
                cat = str(data["category"]).strip().lower()
                if cat not in schema["categories"]:
                    return self._json(400, {"error": f"unknown category: {cat}",
                        "known": schema["categories"]})
                data["category"] = cat
            else:
                data.pop("category", None)
            if "tags" in data and data.get("tags") is not None:
                try:
                    data["tags"] = normalize_tags(data["tags"])
                except ValueError as ex:
                    return self._json(400, {"error": str(ex)})
            else:
                data.pop("tags", None)
            # B3c-rich: optional url (http/https, bounded) + description cap
            if data.get("url") is not None:
                u2 = str(data["url"]).strip()
                if not (u2.startswith("http://") or u2.startswith("https://")) or len(u2) > 300:
                    return self._json(400, {"error": "url must start with http:// or https:// (max 300 chars)"})
                data["url"] = u2
            else:
                data.pop("url", None)
            if data.get("description") is not None:
                desc = str(data["description"]).strip()
                if len(desc) > 500:
                    return self._json(400, {"error": "description too long (max 500 chars)"})
                data["description"] = desc
            else:
                data.pop("description", None)
            allowed = set(schema["required"]) | set(schema.get("optional", [])) | {"vertical"}
            data = {k: d for k, d in data.items() if k in allowed}  # drop unknowns
            with LOCK:
                lid = f"{v[:4]}-{len(LISTINGS)+1}"
                data["id"] = lid
                data["owner"] = p["sub"]   # SERVER-OWNED: authenticated principal, never client-set (anti-spoof for /orders)
                manage_code = "mgr-" + secrets.token_hex(6)   # ownership secret: shown ONCE, stored as sha256 only
                data["manage_code_hash"] = hashlib.sha256(manage_code.encode()).hexdigest()
                if v == "events":
                    data["registered"] = 0   # server-initialized counter
                    data["available"] = True  # default; SOLD OUT derives from registered>=capacity at booking time
                if v == "food": data["available"] = bool(data.get("available", True))
                LISTINGS.append(data)
                _persist_locked()
            return self._json(201, {"ok": True, "id": lid, "manage_code": manage_code,
                "manage_note": "shown ONCE — required to edit or delete this listing; store it now"})
        if path.startswith("/listings/") and path.endswith("/manage"):
            # B3c-ownership: manage code OR logged-in account ownership proves control
            lid = path[len("/listings/"):-len("/manage")]
            code = str(data.get("manage_code", "")).strip()
            action = str(data.get("action", "")).strip().lower()
            if action not in ("edit", "delete"):
                return self._json(400, {"error": "action must be 'edit' or 'delete'"})
            cred = self.headers.get("X-Hub-Token", "")
            p, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False) if cred \
                else (None, "missing X-Hub-Token")
            if not p:
                return self._json(401, {"error": err or "missing X-Hub-Token",
                    "hint": "login via POST /accounts/login {account_code, agent} to act as your account, or send a list token + manage_code"})
            principal = p["sub"]
            with LOCK:
                listing = next((l for l in LISTINGS if l["id"] == lid), None)
                if not listing:
                    return self._json(404, {"error": f"no listing {lid}"})
                is_owner = principal.startswith("acct-") and principal == listing.get("owner")
                if not is_owner:
                    if not code or not hmac.compare_digest(hashlib.sha256(code.encode()).hexdigest(),
                                                           str(listing.get("manage_code_hash", ""))):
                        return self._json(403, {"error": "invalid manage_code for this listing (or login as the owning account)"})
                active = [b for b in BOOKINGS
                          if b.get("listing_id") == lid and b.get("escrow") in ("HELD", "WAIVED")]
                if action == "delete":
                    if active:
                        return self._json(409, {"error": f"listing has {len(active)} active booking(s); resolve (confirm/cancel) first"})
                    LISTINGS.remove(listing)
                    _persist_locked()
                    return self._json(200, {"ok": True, "deleted": lid})
                editable = {"title", "description", "price", "location", "date",
                            "capacity", "category", "tags", "url"}
                changes = {k: data[k] for k in editable if k in data}
                if not changes:
                    return self._json(400, {"error": "no editable fields given",
                                            "editable": sorted(editable)})
                if "price" in changes:
                    try:
                        np = float(changes["price"])
                        if not (np >= 0 and np == np and np not in (float("inf"), float("-inf"))):
                            raise ValueError
                        changes["price"] = np
                    except (TypeError, ValueError):
                        return self._json(400, {"error": "price must be a nonnegative number"})
                if "capacity" in changes:
                    try:
                        nc = int(changes["capacity"])
                    except (TypeError, ValueError):
                        return self._json(400, {"error": "capacity must be an integer"})
                    reg = listing.get("registered", 0) or 0
                    if nc < reg:
                        return self._json(409, {"error": f"capacity below already-registered {reg}"})
                    changes["capacity"] = nc
                if "category" in changes:
                    cat = str(changes["category"]).strip().lower()
                    sch = VERTICAL_SCHEMAS[listing["vertical"]]
                    if cat not in sch["categories"]:
                        return self._json(400, {"error": f"unknown category: {cat}", "known": sch["categories"]})
                    changes["category"] = cat
                if "tags" in changes:
                    try:
                        changes["tags"] = normalize_tags(changes["tags"])
                    except ValueError as ex:
                        return self._json(400, {"error": str(ex)})
                if "url" in changes:
                    u3 = str(changes["url"]).strip()
                    if not (u3.startswith("http://") or u3.startswith("https://")) or len(u3) > 300:
                        return self._json(400, {"error": "url must start with http:// or https:// (max 300 chars)"})
                    changes["url"] = u3
                if "description" in changes:
                    d3 = str(changes["description"]).strip()
                    if len(d3) > 500:
                        return self._json(400, {"error": "description too long (max 500 chars)"})
                    changes["description"] = d3
                if "title" in changes:
                    t3 = str(changes["title"]).strip()
                    if not t3:
                        return self._json(400, {"error": "title cannot be empty"})
                    changes["title"] = t3[:80]
                listing.update(changes)
                _persist_locked()
                return self._json(200, {"ok": True, "edited": lid, "fields": sorted(changes)})
        if path == "/book":
            cred = self.headers.get("X-Hub-Token", "")  # I2: book token required
            p, err = hublib.verify_token(BOOKING_KEY, cred, "book", single_use=False) if cred \
                else (None, "missing X-Hub-Token")
            if not p:
                if cred:  # valid token but wrong action => authenticated, not authorized
                    for other in ("list",):
                        po, _ = hublib.verify_token(BOOKING_KEY, cred, other, single_use=False)
                        if po:
                            return self._json(403, {"error": f"token is for '{other}', not 'book'"})
                return self._json(401, {"error": err or "invalid book token",
                    "hint": "POST /access {agent: '<your-agent>', acts: ['book']} then send token as X-Hub-Token",
                    "note": "interim open bootstrap; production = Midnight zk-personhood (A2)"})
            principal = p["sub"]
            # B3c-accounts: verified accounts carry their human proof server-side
            # (admin-vouch pilot / email-code deploy / Midnight zk A2 production)
            acct_verified = False
            if principal.startswith("acct-"):
                with LOCK:
                    acct = ACCOUNTS.get(principal)
                if acct and acct.get("human_verified"):
                    acct_verified = True
            if not data.get("human_verified") and not acct_verified:
                return self._json(403, {"error": "booking requires verified-human credential",
                    "note": "production: zk-proof of personhood (Midnight); interim: verified EverList account or stub flag"})
            # G4: per-principal fixed-window rate limit (default 10 books / 60s,
            # HUB_RATE_BOOKS_PER_MIN env) - derived from the VERIFIED token principal,
            # not attacker-chosen payload names
            now = time.time()
            with LOCK:
                hist = [t for t in RATE_LIMITS.setdefault(principal, []) if now - t < 60]
                if len(hist) >= RATE_BOOKS_PER_MIN:
                    retry = int(60 - (now - hist[0])) + 1
                    return self._json(429, {"error": f"rate limit: max {RATE_BOOKS_PER_MIN} bookings/min per principal",
                                            "retry_after": retry},
                                      extra_headers={"Retry-After": str(retry)})
                hist.append(now)
                RATE_LIMITS[principal] = hist
            lid = data.get("listing_id")
            with LOCK: listing = next((l for l in LISTINGS if l["id"] == lid), None)
            if not listing: return self._json(404, {"error": f"no listing {lid}"})
            v = listing["vertical"]
            missing = [f for f in VERTICAL_SCHEMAS[v]["booking"]["required"] if f not in data]
            if missing: return self._json(400, {"error": f"missing fields: {missing}"})
            if not data.get("human_verified"):
                return self._json(403, {"error": "booking requires verified-human credential",
                    "note": "production: zk-proof of personhood (Midnight); stub accepts human_verified: true"})
            # I1: field ownership — reject reserved fields, keep only allowlisted client fields
            reserved_seen = [k for k in data if k in RESERVED_BOOKING_FIELDS]
            if reserved_seen:
                return self._json(400, {"error": f"reserved fields rejected: {reserved_seen}"})
            allowed = CLIENT_BOOKING_FIELDS[v] | {"listing_id", "human_verified"}
            unknown = [k for k in data if k not in allowed]
            if unknown:
                return self._json(400, {"error": f"unknown fields rejected: {unknown}",
                    "allowed": sorted(CLIENT_BOOKING_FIELDS[v])})
            qty_raw = data.get("quantity", 1)
            # I3: strict integer quantity — bool/float/str rejected, no silent truncation
            if isinstance(qty_raw, bool) or not isinstance(qty_raw, int) or not (1 <= qty_raw <= 100):
                return self._json(400, {"error": "quantity must be an integer in [1, 100]"})
            qty = qty_raw
            # I4: idempotency — same key + identical payload replays the same booking;
            # same key + different payload -> 409 conflict. Bound to the token principal:
            # agent B can never replay agent A's stored response (it contains secrets).
            idem_key = self.headers.get("Idempotency-Key", "")
            canon = json.dumps({"principal": principal, **data}, sort_keys=True, separators=(",", ":"))
            if idem_key:
                with LOCK:
                    prev = IDEMPOTENCY.get(idem_key)
                    if prev:
                        if prev["hash"] != hashlib.sha256(canon.encode()).hexdigest():
                            return self._json(409, {"error": "Idempotency-Key reused with different payload"})
                        return self._json(201, {**prev["response"], "replayed": True})
            with LOCK:  # I3: single reservation section — check + increment atomic under one LOCK
                listing = next((l for l in LISTINGS if l["id"] == lid), None)
                if not listing: return self._json(404, {"error": f"no listing {lid}"})
                if listing["vertical"] == "events":
                    if listing["registered"] + qty > listing["capacity"]:
                        return self._json(409, {"error": "event full"})
                elif listing["vertical"] == "food" and not listing.get("available", True):
                    return self._json(409, {"error": "listing unavailable"})
                # I3: money as integer minor units internally (convert at the edge)
                price_c = int(round(listing["price"] * 100)) * qty
                fee_c = hub_fee_c(price_c); payout_c = price_c - fee_c
                price = price_c / 100.0; fee = fee_c / 100.0; payout = payout_c / 100.0
                # B3c-waiver: free listings need no payment rail — escrow state WAIVED
                # (verified-human gate still applies; ledger stays complete)
                escrow_state = "WAIVED" if price_c == 0 else "HELD"
                # H2: unguessable booking IDs (no sequential enumeration)
                bid = "bk-" + secrets.token_hex(12)
                secret = secrets.token_hex(16)
                # I1: server-owned fields ONLY; client data enters via explicit allowlist
                priv_fields = {k: d for k, d in data.items() if k in ("attendee", "buyer")}
                pub = {k: (anon_ref() if k in ("attendee", "buyer") else d)
                       for k, d in data.items() if k in CLIENT_BOOKING_FIELDS[v]}
                booking = {"id": bid, "listing_id": lid, "vertical": v,
                    "escrow": escrow_state, "amount": price, "hub_fee": fee,
                    "owner_payout": payout, "quantity": qty, "created": time.time(),
                    "booked_by": principal, **pub}
                # real identity stored ONLY here, retrievable only with the secret
                SECRETS[bid] = {"secret": secret, "private": priv_fields}
                BOOKINGS.append(booking)
                LEDGER.append({"ts": time.time(), "booking": bid, "amount": price,
                    "hub_fee": fee, "owner_payout": payout, "escrow": escrow_state})
                if v == "events": listing["registered"] += qty
                _persist_locked()
            resp = {**booking,
                "booking_secret": secret, "secret_note": "shown ONCE; required to view private details",
                "cancel_token": hublib.mint_token(BOOKING_KEY, "cancel", bid, ttl=7*24*3600),
                "flow": [
                ("free listing — payment WAIVED (no escrow rail)" if escrow_state == "WAIVED" else "escrow HELD (funds locked)"),
                f"confirm: owner POST /book/{{id}}/confirm with X-Hub-Token (mint via POST /admin/tokens, act=confirm)",
                f"cancel: buyer POST /book/{bid}/cancel with X-Hub-Token: cancel_token before fulfillment -> full refund"]}
            if idem_key:
                with LOCK:
                    IDEMPOTENCY[idem_key] = {"hash": hashlib.sha256(canon.encode()).hexdigest(),
                                             "response": resp}
                    _persist_locked()
            return self._json(201, resp)
        if path.startswith("/book/") and path.endswith("/confirm"):
            bid = path.split("/")[2]
            cred = self.headers.get("X-Hub-Token", "")  # I2: owner token required
            if not cred:
                return self._json(401, {"error": "missing X-Hub-Token",
                    "hint": "owner mints a confirm token via POST /admin/tokens {act: 'confirm', booking_id}}"})
            p, err = hublib.verify_token(ADMIN_KEY, cred, "confirm", subject=bid)
            if not p:
                return self._json(403, {"error": err or "token not valid for this booking"})
            with LOCK:
                b = next((x for x in BOOKINGS if x["id"] == bid), None)
                if not b: return self._json(404, {"error": "no booking"})
                if b["escrow"] != "HELD": return self._json(409, {"error": f"escrow is {b['escrow']}"})
                b["escrow"] = "RELEASED"
                for t in LEDGER:
                    if t["booking"] == bid: t["escrow"] = "RELEASED"; t["released_to"] = "owner"
                _persist_locked()
            return self._json(200, {"ok": True, "id": bid, "escrow": "RELEASED",
                "owner_received": b["owner_payout"], "confirmation": f"{bid}-ticket"})
        if path.startswith("/book/") and path.endswith("/cancel"):
            bid = path.split("/")[2]
            cred = self.headers.get("X-Hub-Token", "")  # I2: buyer cancel_token required
            if not cred:
                return self._json(401, {"error": "missing X-Hub-Token",
                    "hint": "use the cancel_token returned at booking time"})
            p, err = hublib.verify_token(BOOKING_KEY, cred, "cancel", subject=bid)
            if not p:
                return self._json(403, {"error": err or "token not valid for this booking"})
            with LOCK:
                b = next((x for x in BOOKINGS if x["id"] == bid), None)
                if not b: return self._json(404, {"error": "no booking"})
                if b["escrow"] != "HELD": return self._json(409, {"error": f"escrow is {b['escrow']}"})
                b["escrow"] = "REFUNDED"
                # I3: restore capacity exactly once, atomically with the escrow transition
                lst = next((l for l in LISTINGS if l["id"] == b.get("listing_id")), None)
                if lst and b["vertical"] == "events":
                    lst["registered"] = max(0, lst["registered"] - b.get("quantity", 1))
                for t in LEDGER:
                    if t["booking"] == bid: t["escrow"] = "REFUNDED"; t["refunded_to"] = "buyer"
                _persist_locked()
            return self._json(200, {"ok": True, "id": bid, "escrow": "REFUNDED"})
        if path == "/admin/tokens":
            # I2: restricted token minting - admin bootstrap key required (constant-time compare)
            cred = self.headers.get("X-Hub-Token", "")
            if not hmac.compare_digest(cred, ADMIN_KEY):
                return self._json(403, {"error": "invalid admin key",
                    "note": "bootstrap: X-Hub-Token must equal HUB_ADMIN_KEY env (dev default 'dev-admin-key-change-me')"})
            act = data.get("act"); sub = data.get("booking_id", "")
            if act not in ("confirm",):  # only confirm minting exposed for now
                return self._json(400, {"error": "act must be 'confirm'"})
            if not sub: return self._json(400, {"error": "booking_id required"})
            tok = hublib.mint_token(ADMIN_KEY, "confirm", sub, ttl=int(data.get("ttl", 3600)))
            return self._json(201, {"ok": True, "token": tok, "act": "confirm", "booking_id": sub})
        return self._json(404, {"error": "not found"})

    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    _load_state()
    print(f"agent-hub-v2 (open/fair) on :{port} - fee {FEE_PCT}%, env {RUN_ENV}, state {STATE_FILE}")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
