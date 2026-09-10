"""agent-hub-v2 - open, fair agent commerce hub (stdlib only, MIT).

Universal booking core: any vertical (events, food, ...) is just a schema.
Fairness is enforced in the protocol:
- hub fee is CAPPED and declared in the manifest (violators are non-conformant)
- every transaction goes to a public ledger (GET /ledger)
- escrow by default: money is held until fulfillment is confirmed
- open participation: no auth gate on the protocol level (identity/staking is a pluggable layer)
"""
import json, os, sys, shutil, threading, time, hmac, hashlib, secrets, base64, binascii
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _EdPriv, Ed25519PublicKey as _EdPub
from cryptography.hazmat.primitives import serialization as _ser
from cryptography.exceptions import InvalidSignature as _InvalidSig
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
IDEMPOTENCY_CAP = 50_000  # HARDENING: bound state.json growth (FIFO evict oldest)
RATE_LIMITS = {}  # G4: principal -> [timestamps] for the 60s window
ID_COUNTERS = {}  # per-vertical id counters - persisted, NEVER decremented (ids never reused after deletes)
# B3c-accounts: chat-native organizer accounts (pilot-grade auth).
# acct-<id> -> {code_hash, bound: [agent names], human_verified, verified_by, created}.
# The account CODE is the credential (shown ONCE, sha256-only); login mints tokens
# with sub=acct-<id>; open /access can NEVER mint acct- principals (forgery wall).
ACCOUNTS = {}
ACCOUNTS_CAP = int(os.environ.get("HUB_ACCOUNTS_CAP", "10000"))  # TOTAL anti-DoS cap (state.json atomically rewritten per mutation) - env-tunable, NOT per-day
# auth endpoints: global fixed-window limits (pilot-grade anti-bruteforce/DoS;
# per-IP is unreliable behind relays, documented honestly in SPEC)
# HARDENING-v2: PoW replaced tight caps as the primary DoS gate — limits are
# generous per-source backstops now (a fair user hits none of them).
# B7: public-read sanity limits. Generous by design — legit agents never feel them.
READ_Q_MAX = 200                                        # search term length cap
READ_PAGE_MAX = int(os.environ.get("HUB_READ_PAGE_MAX", "500"))  # max results per page
READ_LIMIT = int(os.environ.get("HUB_READ_LIMIT", "600"))        # reads/min/source
# B8: state backup rotation. Backup triggers only when the state file exceeds
# HUB_BACKUP_MIN_BYTES (default 1MB) so small dev states stay clean.
BACKUP_MIN_BYTES = int(os.environ.get("HUB_BACKUP_MIN_BYTES", "1000000"))
BACKUP_KEEP = int(os.environ.get("HUB_BACKUP_KEEP", "5"))
# H3: durability. os.replace is atomic but NOT durable — after a power cut the
# last persist may exist only in page cache. fsync the temp file before the
# replace and fsync the directory after it (the rename itself needs it).
# Default ON; HUB_FSYNC=0 disables (benchmarking only).
FSYNC_ENABLED = os.environ.get("HUB_FSYNC", "1") != "0"
AUTH_LIMITS = {"signup": (30, 3600), "login": (120, 60), "rotate": (10, 3600),
                 "email": (5, 3600), "verify": (20, 3600), "recover": (10, 3600),
                 "read": (READ_LIMIT, 60),
                 # H5: write-path backstops (per source) on every mutating route;
                 # env-tunable like READ_LIMIT (tests shrink them; ops can tune)
                 "access": (int(os.environ.get("HUB_LIMIT_ACCESS", "60")), 60),
                 "create": (int(os.environ.get("HUB_LIMIT_CREATE", "60")), 60),
                 "book": (int(os.environ.get("HUB_LIMIT_BOOK", "60")), 60),
                 "manage": (int(os.environ.get("HUB_LIMIT_MANAGE", "60")), 60),
                 "admin": (int(os.environ.get("HUB_LIMIT_ADMIN", "30")), 60),
                 "delete": (int(os.environ.get("HUB_LIMIT_DELETE", "30")), 60)}
# HARDENING-v2: per-source fairness (was: one global bucket per kind — a single
# attacker could deny service to ALL signups by filling the shared window).
AUTH_HITS = {}  # (kind, source_ip) -> [timestamps]
_AUTH_HITS_CAP = 100_000  # bound memory vs source-spoofing floods
TRUST_PROXY = os.environ.get("HUB_TRUST_PROXY", "") == "1"  # behind reverse proxy only


def _source_of(handler):
    """Client source for fairness limiting. Direct socket address by default;
    X-Forwarded-For only when HUB_TRUST_PROXY=1 (reverse-proxy deployments)."""
    if TRUST_PROXY:
        xff = handler.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
    return str(handler.client_address[0]) if handler.client_address else "unknown"


def _paginate(items, query):
    """B7: offset pagination for public reads. Default (no params) returns up to
    READ_PAGE_MAX — identical behavior for small hubs; huge result sets page via
    offset+limit instead of growing responses unboundedly. Returns (slice, off, lim)."""
    try:
        off = max(0, int(query.get("offset", ["0"])[0]))
    except (ValueError, TypeError):
        off = 0
    try:
        lim = int(query.get("limit", [str(READ_PAGE_MAX)])[0])
    except (ValueError, TypeError):
        lim = READ_PAGE_MAX
    lim = max(1, min(lim, READ_PAGE_MAX))
    return items[off:off + lim], off, lim


_SERVER_ONLY_LISTING_FIELDS = frozenset({"manage_code_hash"})


def _pub_listing(l):
    """H9: server-only fields must NEVER reach a public response.
    manage_code_hash = sha256 of the owner's manage code; with it an attacker
    could brute-force the ~48-bit code offline and forge ownership. Internal
    state (LISTINGS) keeps it; every response copy passes through here."""
    return {k: v for k, v in l.items() if k not in _SERVER_ONLY_LISTING_FIELDS}


def _read_gate(handler):
    """B7: coarse per-source read backstop. Caller must NOT hold LOCK."""
    with LOCK:
        return _auth_allow("read", _source_of(handler))


def _gen_check(payload):
    """B1: per-account token generation. rotate / recover/confirm / logout-all bump
    the account's gen; tokens embedding an older gen are dead. Non-account tokens
    (per-booking, /access agent tokens) pass through untouched."""
    if not payload:
        return None, "missing token"
    sub = payload.get("sub", "")
    if sub.startswith("acct-"):
        with LOCK:
            acct = ACCOUNTS.get(sub)
        if not acct:
            return None, "account no longer exists"
        if int(payload.get("gen", 0)) < int(acct.get("gen", 0)):
            return None, "token revoked - account credential rotated or logout-all; log in again"
    return payload, None


def _auth_allow(kind, src="global"):
    """Fixed-window limiter, PER SOURCE. Caller MUST hold LOCK."""
    limit, window = AUTH_LIMITS[kind]
    now = time.time()
    key = (kind, str(src))
    if len(AUTH_HITS) >= _AUTH_HITS_CAP and key not in AUTH_HITS:
        for k in list(AUTH_HITS)[: len(AUTH_HITS) // 10]:
            AUTH_HITS.pop(k, None)
    hist = [t for t in AUTH_HITS.get(key, []) if now - t < window]
    if len(hist) >= limit:
        AUTH_HITS[key] = hist
        return False
    hist.append(now)
    AUTH_HITS[key] = hist
    return True


# HARDENING-v2: proof-of-work cost curve (hashcash-style). Expensive anonymous
# actions (signup, recover) must burn client CPU; legit users pay ~0.3s ONCE,
# attackers pay per attempt — and limits stay generous because PoW is the gate.
POW_DIFFICULTY = {  # leading zero BITS; env-tunable (B2: tests lower it; ops can raise it)
    "signup": int(os.environ.get("HUB_POW_SIGNUP_BITS", "18")),
    "recover": int(os.environ.get("HUB_POW_RECOVER_BITS", "16")),
}  # ~0.3s / ~0.07s client CPU per solution at defaults
POW_CHALLENGES = {}  # challenge -> {exp, kind, used} ; single-use, TTL 10 min
POW_CAP = 100_000
LOGIN_CHALLENGES = {}  # aid -> {ch, exp} ; one live login challenge per account


def _pow_issue(kind):
    ch = secrets.token_hex(16)
    POW_CHALLENGES[ch] = {"exp": time.time() + 600, "kind": kind}
    if len(POW_CHALLENGES) > POW_CAP:
        now = time.time()
        for k in [k for k, v in POW_CHALLENGES.items() if v["exp"] < now][: len(POW_CHALLENGES) // 2]:
            POW_CHALLENGES.pop(k, None)
    return {"algo": "sha256-leading-zeros", "challenge": ch,
            "difficulty": POW_DIFFICULTY[kind], "ttl": 600,
            "hint": "find nonce N (int) with sha256(challenge + str(N)) having <difficulty> leading zero bits; POST it back as pow: {challenge, nonce}"}


def _pow_spend(kind, powobj):
    """Verify+consume one PoW solution. Returns error string or None. Caller holds LOCK."""
    if not isinstance(powobj, dict):
        return "pow required: GET /auth/challenge?kind=" + kind
    ch = str(powobj.get("challenge", ""))
    nonce = powobj.get("nonce")
    rec = POW_CHALLENGES.get(ch)
    if not rec or rec["kind"] != kind:
        return "invalid or expired pow challenge"
    if rec.pop("used", False):
        return "pow challenge already used"
    if time.time() > rec["exp"]:
        POW_CHALLENGES.pop(ch, None)
        return "pow challenge expired"
    if not isinstance(nonce, int) or isinstance(nonce, bool) or abs(nonce) > 10**15:
        return "pow nonce must be an integer"
    digest = hashlib.sha256((ch + str(nonce)).encode()).digest()
    need = POW_DIFFICULTY[kind]
    bits = 0
    for b in digest:
        if b == 0:
            bits += 8; continue
        bits += 8 - b.bit_length()  # leading zero bits of this byte
        break
    if bits < need:
        return f"pow insufficient difficulty (need {need} zero bits)"
    rec["used"] = True
    return None


LOCK = threading.Lock()

# G1: JSON snapshot persistence (atomic write on every mutation, load on start).
# Privacy: SECRETS (real attendee/buyer names) are NEVER persisted - identities stay
# memory-only by design; booking records + ledger + listings + idempotency + token
# nonces survive restarts.


def _backup_locked():
    """B8: rotate state backups. Called at the START of a persist, BEFORE the
    incoming snapshot overwrites STATE_FILE: copies the CURRENT file (the
    pre-persist snapshot) to <state_dir>/backups/state-<ns>.json and keeps the
    newest BACKUP_KEEP. Backup failure must NEVER break persistence."""
    try:
        if not os.path.exists(STATE_FILE) or os.path.getsize(STATE_FILE) < BACKUP_MIN_BYTES:
            return
        bdir = os.path.join(os.path.dirname(STATE_FILE), "backups")
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(STATE_FILE, os.path.join(bdir, f"state-{time.time_ns()}.json"))
        baks = sorted(f for f in os.listdir(bdir)
                      if f.startswith("state-") and f.endswith(".json"))
        for old_b in baks[:-BACKUP_KEEP]:
            try:
                os.remove(os.path.join(bdir, old_b))
            except OSError:
                pass
    except Exception as ex:
        print(f"[BACKUP] warning: rotation failed ({ex})", flush=True)


def _persist_locked():
    """Atomic snapshot write. Caller MUST hold LOCK."""
    _backup_locked()
    snap = {"listings": LISTINGS, "bookings": BOOKINGS, "ledger": LEDGER,
            "idempotency": IDEMPOTENCY, "accounts": ACCOUNTS, "id_counters": ID_COUNTERS,
            "nonces": {n: e for n, e in getattr(hublib, "_NONCES", {}).items()},
            "x402_nonces": (x402verify.snapshot_used_nonces() if PAY_MODE == "testnet" else {}),
            "settlements": (SETTLEMENTS.snapshot() if PAY_MODE == "testnet" else {})}
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(snap, f)
        if FSYNC_ENABLED:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, STATE_FILE)  # atomic on POSIX
    if FSYNC_ENABLED:
        # H3: make the rename itself durable — fsync the containing directory
        dfd = os.open(os.path.dirname(os.path.abspath(STATE_FILE)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


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
        ID_COUNTERS.update(snap.get("id_counters", {}))
        # B3c-email + B1 migration: legacy accounts predate email/gen fields
        _EFIELDS = {"email": None, "email_verified": False, "pending_email": None,
                    "pending_code_hash": None, "pending_exp": 0,
                    "recovery_code_hash": None, "recovery_exp": 0, "gen": 0}
        for _a, _v in ACCOUNTS.items():
            for _k, _d in _EFIELDS.items():
                _v.setdefault(_k, _d)
        hublib._NONCES.update(snap.get("nonces", {}))
        if PAY_MODE == "testnet":
            x402verify.load_used_nonces(snap.get("x402_nonces", {}))
            SETTLEMENTS.load(snap.get("settlements", {}))
        print(f"G1: restored {len(BOOKINGS)} bookings, {len(LEDGER)} ledger entries, "
              f"{len(LISTINGS)} listings from {STATE_FILE}")
    except Exception as ex:
        # HARDENING: corrupt state = stop, never overwrite. Starting fresh would
        # let the first mutation atomically destroy potentially recoverable data.
        sys.stderr.write(
            f"FATAL: state file {STATE_FILE} is corrupt ({ex}).\n"
            "Refusing to start to avoid destroying data.\n"
            "Fix the file or move it aside, then restart.\n")
        sys.exit(78)

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
# H12: bounded rotating request log (ops hygiene; wrapper.py got its own in B4).
# One structured line per response: method, path (query stripped, truncated),
# status, latency, source, request-id. Bodies/headers are NEVER logged; log
# failures are swallowed — observability must never break the hub.
import logging
import logging.handlers
REQ_LOG_FILE = os.environ.get("HUB_REQUEST_LOG",
    os.path.join(os.path.dirname(STATE_FILE), "requests.log"))  # beside state: per-instance isolation
REQ_LOG_MAX = int(os.environ.get("HUB_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
REQ_LOG_N = int(os.environ.get("HUB_LOG_BACKUPS", "3"))
_reqlog = None
try:  # H12: a logging problem must never prevent startup
    os.makedirs(os.path.dirname(REQ_LOG_FILE), exist_ok=True)
    _reqlog = logging.getLogger("hub.requests")
    _reqlog.setLevel(logging.INFO)
    _reqlog.addHandler(logging.handlers.RotatingFileHandler(
        REQ_LOG_FILE, maxBytes=REQ_LOG_MAX, backupCount=REQ_LOG_N))
    _reqlog.propagate = False
except Exception:
    _reqlog = None  # hub runs unlogged rather than not at all
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
# B3c-email: account email binding + recovery. Modes: off (default) | log (dev: code in hub log) | smtp
EMAIL_MODE = os.environ.get("HUB_EMAIL_MODE", "off")
if EMAIL_MODE not in ("off", "log", "smtp"):
    sys.stderr.write(f"FATAL: HUB_EMAIL_MODE must be off|log|smtp, got {EMAIL_MODE}\n")
    sys.exit(78)
SMTP_HOST = os.environ.get("HUB_SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("HUB_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("HUB_SMTP_USER", "")
SMTP_PASS = os.environ.get("HUB_SMTP_PASS", "")
MAIL_FROM = os.environ.get("HUB_MAIL_FROM", SMTP_USER or "everlist@localhost")


def _send_email(to, subject, body):
    """Honest delivery: off/log are dev modes (label says so); smtp really sends."""
    if EMAIL_MODE == "off":
        return "off"
    if EMAIL_MODE == "log":
        print(f"[EMAIL:log] to={to} subject={subject!r} body={body!r}", flush=True)
        return "logged"
    import smtplib
    msg = f"From: {MAIL_FROM}\r\nTo: {to}\r\nSubject: {subject}\r\n\r\n{body}"
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as srv:
        srv.starttls()
        srv.login(SMTP_USER, SMTP_PASS)
        srv.sendmail(MAIL_FROM, [to], msg)
    return "sent"


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

# ---- H13: machine-readable API contract (OpenAPI 3.1) -----------------------
def _openapi_spec():
    """H13: OpenAPI 3.1 contract, built from the VERIFIED route inventory.
    test_h13_openapi.py proves every documented path+method is really handled
    (never the generic 404 catch-all) — the spec cannot lie about a route."""
    def op(summary, tag, sec=None, note=None):
        o = {"summary": summary, "tags": [tag]}
        if sec:
            o["security"] = sec
        if note:
            o["description"] = note
        return o
    tok = [{"X-Hub-Token": []}]
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "EverList Hub API",
            "version": "agent-hub/0.2",
            "description": "Open, community-driven commerce hub for AI agents. "
                           "Credentials travel ONLY in the X-Hub-Token header; writes carry "
                           "an Idempotency-Key. Normative contract: SPEC.md."},
        "tags": [{"name": t} for t in
                 ("discovery", "listings", "bookings", "accounts", "payments", "registry", "admin")],
        "paths": {
            "/.well-known/agent-hub.json": {"get": op("Discovery manifest", "discovery")},
            "/manifest.json": {"get": op("Legacy manifest alias", "discovery")},
            "/openapi.json": {"get": op("This contract (OpenAPI 3.1)", "discovery")},
            "/verticals": {"get": op("Vertical schema registry", "discovery")},
            "/listings": {
                "get": op("Public listings (query: vertical, archived)", "listings",
                          note="?archived=1 is OWNER-ONLY (auth required)"),
                "post": op("Create listing (returns manage_code ONCE)", "listings", sec=tok)},
            "/listings/{id}": {"get": op("One listing, full rich record", "listings",
                                        note="404 unknown / 410 archived")},
            "/listings/{id}/manage": {"post": op("Edit/archive/unarchive/delete a listing", "listings", sec=tok)},
            "/search": {"get": op("Search listings (q, category, max_price, ...)", "listings")},
            "/bookings": {"get": op("Your bookings (principal-scoped)", "bookings", sec=tok)},
            "/bookings/{id}": {"get": op("Booking status (buyer or listing owner)", "bookings", sec=tok,
                                        note="unknown-or-not-yours = indistinguishable 404 (no existence oracle)")},
            "/orders": {"get": op("Incoming orders for listings you own", "bookings", sec=tok)},
            "/book/{id}": {"get": op("Private booking details", "bookings", sec=tok,
                                    note="credential = booking secret (shown once at creation), via X-Hub-Token header")},
            "/book": {"post": op("Create booking (escrow HELD / WAIVED at price 0)", "bookings", sec=tok,
                                 note="Idempotency-Key supported; verified-human gate applies")},
            "/book/{id}/confirm": {"post": op("Owner confirms booking (escrow RELEASE)", "bookings", sec=tok)},
            "/book/{id}/cancel": {"post": op("Buyer cancels pre-fulfillment (full refund)", "bookings", sec=tok)},
            "/access": {"post": op("Bootstrap tokens for an agent identity (interim open)", "accounts",
                                  note="acct- principals refused; accounts use /accounts/login")},
            "/accounts/signup": {"post": op("Create account (PoW-gated; keypair or legacy code)", "accounts")},
            "/accounts/login": {"post": op("Login (ed25519 challenge-response or legacy code)", "accounts")},
            "/accounts/vouch": {"post": op("Operator vouches for a human (pilot-era)", "accounts")},
            "/accounts/rotate": {"post": op("Rotate account code (old code dies instantly)", "accounts")},
            "/accounts/logout-all": {"post": op("Revoke all tokens (generation bump)", "accounts")},
            "/accounts/email/bind": {"post": op("Bind recovery email (code sent)", "accounts")},
            "/accounts/email/verify": {"post": op("Verify email code", "accounts")},
            "/accounts/email/recover": {"post": op("Request recovery code (anti-enumeration)", "accounts")},
            "/accounts/email/recover/confirm": {"post": op("Confirm recovery -> NEW account code", "accounts")},
            "/accounts/me": {"delete": op("Account self-deletion (GDPR-style; ledger survives pseudonymously)", "accounts", sec=tok)},
            "/premium/events": {"get": op("Premium data (x402 payment)", "payments",
                                         note="402 + payment instructions without a valid payment header")},
            "/ledger": {"get": op("Public append-only money ledger (pseudonymous)", "registry")},
            "/registry": {"get": op("Open hub registry (self-listed)", "registry")},
            "/challenge": {"get": op("Registry ownership proof (echo nonce)", "registry")},
            "/auth/challenge": {"get": op("Signup PoW / login challenges", "accounts",
                                         note="?kind=signup | ?kind=login&pubkey=<64hex>")},
            "/admin/tokens": {"post": op("Operator: mint action tokens (admin key)", "admin")},
        },
        "components": {"securitySchemes": {"X-Hub-Token": {
            "type": "apiKey", "in": "header", "name": "X-Hub-Token"}}},
    }


class Handler(BaseHTTPRequestHandler):
    def _json(self, code, data, extra_headers=None):
        body = json.dumps(data, indent=2).encode()
        rid = "req-" + secrets.token_hex(8)  # H12: per-request trace id
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Request-Id", rid)  # H12: echoed for support/traceability
        for k, v in (extra_headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        if _reqlog is not None:  # H12: bounded request log — never breaks the hub
            try:
                p = self.path.split("?", 1)[0][:120]  # query NEVER logged (challenges/nonces), truncated
                lat = (time.time() - getattr(self, "_t0", time.time())) * 1000.0
                _reqlog.info("%s %s -> %d %.1fms src=%s rid=%s",
                             getattr(self, "command", "?"), p, code, lat,
                             _source_of(self), rid)
            except Exception:
                pass

    def do_GET(self):
        self._t0 = time.time()  # H12: latency start
        u = urlparse(self.path)
        if u.path == "/auth/challenge":
            """HARDENING-v2: challenge issuance for cost curves.
            kind=signup -> sha256 PoW challenge (must be solved to signup)
            kind=login&pubkey=<hex> -> single-use ed25519 login challenge"""
            q = parse_qs(u.query)
            kind = q.get("kind", [""])[0]
            if kind in ("signup", "recover"):
                with LOCK:
                    return self._json(200, _pow_issue(kind))
            if kind == "login":
                pubkey_hex = q.get("pubkey", [""])[0].strip().lower()
                if len(pubkey_hex) != 64:
                    return self._json(400, {"error": "kind=login needs pubkey=<64 hex>"})
                with LOCK:
                    aid = next((a for a, v in ACCOUNTS.items() if v.get("pubkey") == pubkey_hex), None)
                    if not aid:
                        return self._json(404, {"error": "unknown pubkey"})
                    ch = secrets.token_hex(16)
                    LOGIN_CHALLENGES[aid] = {"ch": ch, "exp": time.time() + 120}
                    return self._json(200, {"algo": "ed25519", "challenge": ch,
                        "ttl": 120, "msg": "sign b'everlist-login:' + challenge with your seed's private key; POST sig (128 hex) to /accounts/login with pubkey + agent"})
            return self._json(400, {"error": "kind must be signup or login"})
        if u.path == "/.well-known/agent-hub.json":
            # standard discovery location - agents probe any domain for this
            return self._json(200, {
                "protocol": "agent-hub/0.2", "open_source": "MIT",
                "api_contract": "/openapi.json",
                "hub": "agent-hub-v2",
                "description": "Open, community-driven commerce hub for AI agents. Universal booking core, per-vertical schemas.",
                "auth": {
            "kind": "crypto-accounts",
            "contract": "SPEC 12a: Ed25519 keypair accounts, challenge-response login, PoW-gated signup",
            "challenge": "/auth/challenge",
            "signup": "/accounts/signup"
        },
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
                    rich = [{**_pub_listing(l), "premium_meta": {"owner_public": l.get("owner", ""),
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
                rich = [{**_pub_listing(l), "premium_meta": {"owner_public": l.get("owner", ""),
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
        if u.path == "/openapi.json":
            # H13: machine-readable API contract — agents read it natively
            return self._json(200, _openapi_spec())
        if u.path == "/verticals":
            return self._json(200, {"verticals": VERTICAL_SCHEMAS})
        if u.path == "/listings":
            if not _read_gate(self):
                return self._json(429, {"error": "too many read requests — slow down (retry shortly)"})
            v = parse_qs(u.query).get("vertical", [""])[0]
            show_arch = parse_qs(u.query).get("archived", [""])[0] in ("1", "true")
            if show_arch:
                # HARDENING: archived listings are OWNER-ONLY. Audit finding:
                # previously readable by anyone (leaked hidden listings).
                cred = self.headers.get("X-Hub-Token", "")
                payload, err = None, "missing X-Hub-Token"
                if cred:
                    for act in ("list", "book"):
                        payload, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                        if payload: break
                if payload:
                    payload, err = _gen_check(payload)  # B1
                if not payload:
                    return self._json(401, {"error": "archived listings are owner-only",
                        "hint": "send X-Hub-Token (login token or /access list token)"})
                me = payload["sub"]
                with LOCK:
                    res = [_pub_listing(l) for l in LISTINGS
                           if l.get("archived") and l.get("owner") == me
                           and (not v or l["vertical"] == v)]
                return self._json(200, {"count": len(res), "listings": res})
            with LOCK:
                res = [_pub_listing(l) for l in LISTINGS
                       if not l.get("archived") and (not v or l["vertical"] == v)]
            total = len(res)
            res, off, lim = _paginate(res, parse_qs(u.query))
            return self._json(200, {"count": total, "offset": off, "limit": lim,
                                    "returned": len(res), "listings": res})
        if u.path.startswith("/listings/"):
            # H9: single-listing fetch, full rich record. Unknown -> 404;
            # archived -> 410 Gone (exists, not publicly available).
            if not _read_gate(self):
                return self._json(429, {"error": "too many read requests — slow down (retry shortly)"})
            lid = u.path[len("/listings/"):]
            with LOCK:
                l = next((x for x in LISTINGS if x["id"] == lid), None)
            if not l:
                return self._json(404, {"error": f"no listing {lid}"})
            if l.get("archived"):
                return self._json(410, {"error": f"listing {lid} archived — its owner can unarchive it"})
            return self._json(200, _pub_listing(l))
        if u.path == "/search":
            """Faceted search: q (substring) + structured filters.
            All filters AND-combined; agents can discover vocab via /verticals."""
            if not _read_gate(self):
                return self._json(429, {"error": "too many read requests — slow down (retry shortly)"})
            q = parse_qs(u.query)
            term = q.get("q", [""])[0].lower()
            if len(term) > READ_Q_MAX:
                return self._json(400, {"error": f"q too long (max {READ_Q_MAX} chars)"})
            fvert = q.get("vertical", [""])[0]
            fcat = q.get("category", [""])[0].strip().lower()
            ftag = q.get("tag", [""])[0].strip().lower()
            floc = q.get("location", [""])[0].strip().lower()
            fmax = q.get("max_price", [""])[0]
            with LOCK:
                res = [l for l in LISTINGS
                       if not l.get("archived")
                       and (not term or term in json.dumps(l).lower())
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
            total = len(res)
            res, off, lim = _paginate(res, parse_qs(u.query))
            return self._json(200, {"count": total, "offset": off, "limit": lim,
                "returned": len(res), "filters": {
                "q": term, "vertical": fvert, "category": fcat, "tag": ftag,
                "location": floc, "max_price": fmax or None}, "listings": [_pub_listing(x) for x in res]})
        if u.path == "/bookings":
            cred = self.headers.get("X-Hub-Token", "")  # I2: token required, principal-scoped
            payload, err = None, "missing X-Hub-Token"
            if cred:
                for act in ("book", "list"):
                    payload, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                    if payload: break
            if payload:
                payload, err = _gen_check(payload)  # B1
            if not payload:
                return self._json(401, {"error": err or "invalid token",
                    "hint": "POST /access {agent: '<your-agent>'} then send token as X-Hub-Token"})
            me = payload["sub"]
            with LOCK:
                mine = [b for b in BOOKINGS if b.get("booked_by") == me]
            return self._json(200, {"bookings": mine, "principal": me,
                "note": "principal-scoped: you see only your own bookings"})
        if u.path.startswith("/bookings/"):
            # H10: single-booking status poll. Participant (buyer) or listing-owner
            # only. Unknown-or-not-yours is an indistinguishable 404 (no existence
            # oracle). Projection is pseudonymous; private details stay secret-gated
            # via GET /book/{id} + booking secret.
            if not _read_gate(self):
                return self._json(429, {"error": "too many read requests — slow down (retry shortly)"})
            cred = self.headers.get("X-Hub-Token", "")
            payload, err = None, "missing X-Hub-Token"
            if cred:
                for act in ("book", "list"):
                    payload, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                    if payload: break
            if payload:
                payload, err = _gen_check(payload)  # B1
            if not payload:
                return self._json(401, {"error": err or "invalid token",
                    "hint": "send your login/agent token as X-Hub-Token"})
            me = payload["sub"]
            bid = u.path[len("/bookings/"):]
            with LOCK:
                b = next((x for x in BOOKINGS if x["id"] == bid), None)
                allowed = bool(b) and (b.get("booked_by") == me or
                    any(l.get("owner") == me and l["id"] == b.get("listing_id") for l in LISTINGS))
            if not allowed:
                return self._json(404, {"error": f"no booking {bid} visible to you (unknown, or you are not the buyer/owner)"})
            return self._json(200, {**b, "view": "buyer" if b["booked_by"] == me else "owner",
                "note": "pseudonymous projection; private details remain secret-gated (GET /book/{id})"})
        if u.path == "/orders":
            """Merchant-scoped incoming orders (E1/E2 gap fix): bookings made
            against listings this principal owns. Privacy projection applies:
            pseudonymous refs only — real buyer names stay in the secret store."""
            cred = self.headers.get("X-Hub-Token", "")
            payload, err = None, "missing X-Hub-Token"
            if cred:
                payload, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False)
                if payload:
                    payload, err = _gen_check(payload)  # B1
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
        self._t0 = time.time()  # H12: latency start
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
            # H5: per-source backstop — token minting must not be free (counts
            # every attempt, before validation)
            with LOCK:
                if not _auth_allow("access", _source_of(self)):
                    return self._json(429, {"error": "access rate limit reached for your source, retry later"})
            # I2 interim bootstrap: open issuance PRE-personhood (documented; replaced by Midnight A2)
            agent = str(data.get("agent", "")).strip()
            if not agent or len(agent) > 128:
                return self._json(400, {"error": "agent name required (max 128 chars)"})
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
            # B3c-accounts v2: PoW-gated. Two kinds:
            #  keypair (default): client generates ed25519 seed OFF-server; we store
            #    ONLY the public key. Nothing stealable on the server, ever.
            #  code (legacy): server-minted code, sha256 at rest (kept for compat).
            agent = str(data.get("agent", "")).strip()
            if not agent or len(agent) > 128 or agent.lower().startswith("acct-"):
                return self._json(400, {"error": "agent name required (max 128 chars, no acct- prefix)"})
            pubkey_hex = str(data.get("pubkey", "")).strip().lower()
            if pubkey_hex:
                if len(pubkey_hex) != 64:
                    return self._json(400, {"error": "pubkey must be 64 hex chars (ed25519 raw public key, 32 bytes)"})
                try:
                    bytes.fromhex(pubkey_hex)
                except ValueError:
                    return self._json(400, {"error": "pubkey must be hex"})
            pow_err = None
            with LOCK:
                if len(ACCOUNTS) >= ACCOUNTS_CAP:
                    return self._json(429, {"error": "hub at account capacity; contact the operator"})
                src = _source_of(self)
                if not _auth_allow("signup", src):
                    return self._json(429, {"error": "signup rate limit reached for your source, retry later"})
                pow_err = _pow_spend("signup", data.get("pow"))
                if pow_err:
                    return self._json(400, {"error": pow_err,
                        "hint": "GET /auth/challenge?kind=signup, solve, POST pow={challenge, nonce}"})
                if pubkey_hex and any(v.get("pubkey") == pubkey_hex for v in ACCOUNTS.values()):
                    return self._json(409, {"error": "pubkey already registered — log in instead"})
                aid = "acct-" + secrets.token_hex(4)
                while aid in ACCOUNTS:
                    aid = "acct-" + secrets.token_hex(4)
                acct = {"bound": [agent], "human_verified": False,
                        "verified_by": None, "created": time.time(),
                        "email": None, "email_verified": False,
                        "pending_email": None, "pending_code_hash": None,
                        "pending_exp": 0, "recovery_code_hash": None, "recovery_exp": 0}
                if pubkey_hex:
                    acct["kind"] = "keypair"
                    acct["pubkey"] = pubkey_hex
                else:
                    acct["kind"] = "code"
                    code = "acct-" + secrets.token_hex(8)
                    acct["code_hash"] = hashlib.sha256(code.encode()).hexdigest()
                ACCOUNTS[aid] = acct
                _persist_locked()
            if pubkey_hex:
                return self._json(201, {"account_id": aid, "kind": "keypair", "pubkey": pubkey_hex,
                    "account_code": None,
                    "code_note": "keypair account: the server stores ONLY your public key; your seed never leaves your device — we cannot lose or leak it"})
            return self._json(201, {"account_id": aid, "kind": "code", "account_code": code,
                "human_verified": False,
                "code_note": "shown ONCE — this code OWNS the account; store it like a seed phrase",
                "next": "verify as human: hub operator vouch (pilot) or POST /accounts/verify email code (deploy); login from any chat: POST /accounts/login {account_code, agent}"})
        if path == "/accounts/login":
            agent = str(data.get("agent", "")).strip()
            if not agent or len(agent) > 128 or agent.lower().startswith("acct-"):
                return self._json(400, {"error": "agent required (max 128 chars, no acct- prefix)"})
            code = str(data.get("account_code", "")).strip()
            pubkey_hex = str(data.get("pubkey", "")).strip().lower()
            with LOCK:
                src = _source_of(self)
                if not _auth_allow("login", src):
                    return self._json(429, {"error": "login rate limit reached for your source, retry later"})
                if pubkey_hex:
                    # HARDENING-v2: challenge-response. Server proves nothing secret is needed.
                    aid = next((a for a, v in ACCOUNTS.items() if v.get("pubkey") == pubkey_hex), None)
                    if not aid:
                        return self._json(403, {"error": "unknown pubkey"})
                    lch = LOGIN_CHALLENGES.pop(aid, None)
                    if not lch or time.time() > lch["exp"]:
                        return self._json(403, {"error": "no active login challenge — GET /auth/challenge?kind=login&pubkey=<hex> first"})
                    sig = str(data.get("sig", ""))
                    if len(sig) != 128:
                        return self._json(403, {"error": "sig must be 128 hex chars (ed25519 signature)"})
                    try:
                        vk = _EdPub.from_public_bytes(bytes.fromhex(pubkey_hex))
                        vk.verify(bytes.fromhex(sig), b"everlist-login:" + lch["ch"].encode())
                    except (_InvalidSig, ValueError, binascii.Error):
                        return self._json(403, {"error": "signature invalid"})
                else:
                    # legacy code login (pre-keypair accounts + recovery fallback)
                    if not code:
                        return self._json(400, {"error": "account_code or pubkey required"})
                    ch = hashlib.sha256(code.encode()).hexdigest()
                    aid = next((a for a, v in ACCOUNTS.items() if hmac.compare_digest(v.get("code_hash", ""), ch)), None)
                    if not aid:
                        return self._json(403, {"error": "invalid account code"})
                if agent not in ACCOUNTS[aid]["bound"]:
                    if len(ACCOUNTS[aid]["bound"]) >= 25:  # binding cap: no unbounded list growth
                        return self._json(409, {"error": "agent binding limit (25) reached for this account"})
                    ACCOUNTS[aid]["bound"].append(agent)  # multi-agent binding (agent-led onboarding)
                _persist_locked()
            acts = ["book", "list"]
            gen = ACCOUNTS[aid].get("gen", 0)
            toks = {a: hublib.mint_token(BOOKING_KEY, a, aid, ttl=24*3600,
                                         extra={"gen": gen}) for a in acts}
            return self._json(200, {"account_id": aid, "agent": agent,
                "human_verified": ACCOUNTS[aid]["human_verified"],
                "tokens": toks, "ttl": 24*3600,
                "note": "tokens act AS the account (sub=acct-<id>) for 24h; edit/delete need no per-listing codes"})
        if path == "/accounts/vouch":
            # H5: admin-key brute-force backstop — failed attempts count
            with LOCK:
                if not _auth_allow("admin", _source_of(self)):
                    return self._json(429, {"error": "admin rate limit reached for your source, retry later"})
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
                if not _auth_allow("rotate", _source_of(self)):
                    return self._json(429, {"error": "rotate rate limit reached for your source, retry later"})
                aid = next((a for a, v in ACCOUNTS.items() if v.get("code_hash") and hmac.compare_digest(v["code_hash"], ch)), None)
                if not aid:
                    return self._json(403, {"error": "invalid account code"})
                new_code = "acct-" + secrets.token_hex(8)
                ACCOUNTS[aid]["code_hash"] = hashlib.sha256(new_code.encode()).hexdigest()
                ACCOUNTS[aid]["rotated"] = time.time()
                ACCOUNTS[aid]["gen"] = ACCOUNTS[aid].get("gen", 0) + 1  # B1: revoke old tokens
                LOGIN_CHALLENGES.pop(aid, None)
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid, "account_code": new_code,
                "code_note": "OLD CODE IS NOW INVALID and every previously issued login token is REVOKED — new code shown ONCE, store it"})
        if path == "/accounts/logout-all":
            # B1: revoke EVERY login token of this account (all agents, chats, devices).
            cred = self.headers.get("X-Hub-Token", "")
            p, err = None, "missing X-Hub-Token"
            if cred:
                for act in ("list", "book"):
                    p, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                    if p: break
            if p:
                p, err = _gen_check(p)
            if not p or not str(p.get("sub", "")).startswith("acct-"):
                return self._json(401, {"error": err or "account token required",
                    "hint": "send a login token as X-Hub-Token"})
            aid = p["sub"]
            with LOCK:
                if aid not in ACCOUNTS:
                    return self._json(404, {"error": "no account"})
                ACCOUNTS[aid]["gen"] = ACCOUNTS[aid].get("gen", 0) + 1
                LOGIN_CHALLENGES.pop(aid, None)
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid,
                "note": "every previously issued login token (all agents/devices) is now revoked — log in again where needed"})
        if path == "/accounts/email/bind":
            # bind/replace the recovery email. Logged-in account OR current code proves control.
            code = str(data.get("account_code", "")).strip()
            email = str(data.get("email", "")).strip().lower()
            if not email or "@" not in email or "." not in email.rsplit("@", 1)[1] or len(email) > 254 or " " in email:
                return self._json(400, {"error": "valid email required (name@domain.tld)"})
            ch = hashlib.sha256(code.encode()).hexdigest() if code else None
            cred = self.headers.get("X-Hub-Token", "")
            p, _err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False) if cred else (None, None)
            if p:
                p, _err = _gen_check(p)  # B1: a stale token never proves identity
            with LOCK:
                if p and p["sub"].startswith("acct-") and p["sub"] in ACCOUNTS:
                    aid = p["sub"]
                else:
                    aid = next((a for a, v in ACCOUNTS.items() if ch and v.get("code_hash") and hmac.compare_digest(v["code_hash"], ch)), None)
                if not aid:
                    return self._json(403, {"error": "login token or valid account_code required"})
                if not _auth_allow("email", _source_of(self)):
                    return self._json(429, {"error": "email rate limit reached for your source, retry later"})
                # if another VERIFIED account already holds this email: refuse (no takeover)
                for a, v in ACCOUNTS.items():
                    if a != aid and v["email"] == email and v["email_verified"]:
                        return self._json(409, {"error": "email already bound to another verified account"})
                vcode = secrets.token_hex(3).upper()   # 6 hex chars
                ACCOUNTS[aid]["pending_email"] = email
                ACCOUNTS[aid]["pending_code_hash"] = hashlib.sha256(vcode.encode()).hexdigest()
                ACCOUNTS[aid]["pending_exp"] = time.time() + 900   # 15 min
                _persist_locked()
                try:
                    delivery = _send_email(email, "EverList email verification",
                        f"Your EverList verification code: {vcode}\nValid 15 minutes. If you did not request this, ignore it.")
                except Exception as ex:
                    return self._json(502, {"error": f"email delivery failed: {ex}"})
            return self._json(200, {"ok": True, "account_id": aid, "email": email,
                "delivery": delivery, "note": "verification code sent - confirm with POST /accounts/email/verify {email, code}"})
        if path == "/accounts/email/verify":
            email = str(data.get("email", "")).strip().lower()
            vcode = str(data.get("code", "")).strip().upper()
            ch = hashlib.sha256(vcode.encode()).hexdigest() if vcode else None
            with LOCK:
                if not _auth_allow("verify", _source_of(self)):
                    return self._json(429, {"error": "verify rate limit reached for your source, retry later"})
                aid = next((a for a, v in ACCOUNTS.items()
                            if v.get("pending_email") == email and v.get("pending_code_hash")
                            and hmac.compare_digest(v["pending_code_hash"], ch or "x")
                            and time.time() < v["pending_exp"]), None)
                if not aid:
                    return self._json(403, {"error": "invalid or expired verification code"})
                ACCOUNTS[aid]["email"] = email
                ACCOUNTS[aid]["email_verified"] = True
                ACCOUNTS[aid]["pending_email"] = None
                ACCOUNTS[aid]["pending_code_hash"] = None
                ACCOUNTS[aid]["pending_exp"] = 0
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid, "email": email, "email_verified": True,
                "note": "recovery enabled: POST /accounts/email/recover {email} if you ever lose the account code"})
        if path == "/accounts/email/recover":
            # request: ALWAYS answer the same way (no account enumeration)
            email = str(data.get("email", "")).strip().lower()
            if not email:
                return self._json(400, {"error": "email required"})
            with LOCK:
                if not _auth_allow("recover", _source_of(self)):
                    return self._json(429, {"error": "recover rate limit reached for your source, retry later"})
                # HARDENING-v2: PoW cost on recovery requests (email sends are expensive)
                pw = _pow_spend("recover", data.get("pow"))
                if pw:
                    return self._json(400, {"error": pw,
                        "hint": "GET /auth/challenge?kind=recover, solve, POST pow={challenge, nonce}"})
                aid = next((a for a, v in ACCOUNTS.items() if v.get("email") == email and v.get("email_verified")), None)
                if not aid:
                    return self._json(200, {"ok": True, "delivery": "suppressed",
                        "note": "if that email is bound to an account, a recovery code was sent"})
                rcode = secrets.token_hex(3).upper()
                ACCOUNTS[aid]["recovery_code_hash"] = hashlib.sha256(rcode.encode()).hexdigest()
                ACCOUNTS[aid]["recovery_exp"] = time.time() + 900
                _persist_locked()
                try:
                    delivery = _send_email(email, "EverList account recovery",
                        f"Your EverList recovery code: {rcode}\nValid 15 minutes.\nConfirm with email + code + a new code at /accounts/email/recover/confirm.")
                except Exception as ex:
                    return self._json(502, {"error": f"email delivery failed: {ex}"})
            return self._json(200, {"ok": True, "delivery": delivery,
                "note": "if that email is bound to an account, a recovery code was sent"})
        if path == "/accounts/email/recover/confirm":
            email = str(data.get("email", "")).strip().lower()
            rcode = str(data.get("code", "")).strip().upper()
            pubkey_hex = str(data.get("pubkey", "")).strip().lower()
            if pubkey_hex:
                if len(pubkey_hex) != 64:
                    return self._json(400, {"error": "pubkey must be 64 hex chars"})
                try:
                    bytes.fromhex(pubkey_hex)
                except ValueError:
                    return self._json(400, {"error": "pubkey must be hex"})
            new_code = "acct-" + secrets.token_hex(8)
            ch = hashlib.sha256(rcode.encode()).hexdigest() if rcode else None
            nch = hashlib.sha256(new_code.encode()).hexdigest()
            with LOCK:
                if not _auth_allow("recover", _source_of(self)):
                    return self._json(429, {"error": "recover rate limit reached for your source, retry later"})
                aid = next((a for a, v in ACCOUNTS.items()
                            if v.get("email") == email and v.get("email_verified") and v.get("recovery_code_hash")
                            and hmac.compare_digest(v["recovery_code_hash"], ch or "x")
                            and time.time() < v["recovery_exp"]), None)
                if not aid:
                    return self._json(403, {"error": "invalid or expired recovery code"})
                rec = ACCOUNTS[aid]
                LOGIN_CHALLENGES.pop(aid, None)
                if rec.get("kind") == "keypair":
                    # HARDENING-v2: recovery == key rotation — old seed dies, new seed rules
                    if not pubkey_hex:
                        return self._json(400, {"error": "keypair account: send pubkey=<64 hex> of your NEW seed"})
                    if any(a != aid and v.get("pubkey") == pubkey_hex for a, v in ACCOUNTS.items()):
                        return self._json(409, {"error": "pubkey already registered"})
                    rec["pubkey"] = pubkey_hex
                    rec["gen"] = rec.get("gen", 0) + 1  # B1: old seed's tokens die
                    rec["recovery_code_hash"] = None
                    rec["recovery_exp"] = 0
                    _persist_locked()
                    return self._json(200, {"ok": True, "account_id": aid, "kind": "keypair",
                        "code_note": "pubkey rotated — OLD SEED INVALID; sign a fresh challenge with the new seed to log in"})
                rec["code_hash"] = nch          # recovery == rotation: old code dies
                rec["gen"] = rec.get("gen", 0) + 1  # B1: revoke old tokens
                rec["recovery_code_hash"] = None
                rec["recovery_exp"] = 0
                _persist_locked()
                return self._json(200, {"ok": True, "account_id": aid, "account_code": new_code,
                    "code_note": "shown ONCE — store it; OLD CODE INVALID and old login tokens REVOKED"})
        if path == "/listings":
            cred = self.headers.get("X-Hub-Token", "")  # I2: list token required
            p, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False) if cred \
                else (None, "missing X-Hub-Token")
            if p:
                p, err = _gen_check(p)  # B1
            if not p:
                return self._json(401, {"error": err or "invalid list token",
                    "hint": "POST /access {agent: '<your-agent>', acts: ['list']} then send token as X-Hub-Token"})
            # H5: per-source backstop — the per-principal cap (3) is bypassable
            # via freely-minted principals pre-personhood; flooding counts here
            with LOCK:
                if not _auth_allow("create", _source_of(self)):
                    return self._json(429, {"error": "listing creation rate limit reached for your source, retry later"})
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
                _pref = v[:4]
                _n = ID_COUNTERS.get(_pref, 0)
                if not _n:  # seed once from legacy state (pre-counter listings)
                    _nums = [int(l["id"].rsplit("-", 1)[1]) for l in LISTINGS
                             if l["id"].startswith(_pref + "-") and l["id"].rsplit("-", 1)[1].isdigit()]
                    _n = max(_nums) if _nums else 0
                _n += 1
                ID_COUNTERS[_pref] = _n
                lid = f"{_pref}-{_n}"  # monotonic: never reused, even after deletes
                data["id"] = lid
                data["owner"] = p["sub"]   # SERVER-OWNED: authenticated principal, never client-set (anti-spoof for /orders)
                manage_code = "mgr-" + secrets.token_hex(8)   # ownership secret (64-bit): shown ONCE, stored as sha256 only — NEVER echoed (H9 _pub_listing)
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
            if action not in ("edit", "delete", "archive", "unarchive"):
                return self._json(400, {"error": "action must be 'edit', 'delete', 'archive' or 'unarchive'"})
            cred = self.headers.get("X-Hub-Token", "")
            p, err = hublib.verify_token(BOOKING_KEY, cred, "list", single_use=False) if cred \
                else (None, "missing X-Hub-Token")
            if p:
                p, err = _gen_check(p)  # B1
            if not p:
                return self._json(401, {"error": err or "missing X-Hub-Token",
                    "hint": "login via POST /accounts/login {account_code, agent} to act as your account, or send a list token + manage_code"})
            principal = p["sub"]
            # H5: manage-code brute-force backstop — every attempt counts,
            # wrong-code guesses included
            with LOCK:
                if not _auth_allow("manage", _source_of(self)):
                    return self._json(429, {"error": "manage rate limit reached for your source, retry later"})
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
                if action in ("archive", "unarchive"):
                    listing["archived"] = (action == "archive")
                    _persist_locked()
                    note = ("hidden from search; existing bookings stay fulfillable"
                            if listing["archived"] else "visible again")
                    return self._json(200, {"ok": True, "id": lid, "archived": listing["archived"], "note": note})
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
            p, gerr = _gen_check(p)  # B1
            if not p:
                return self._json(401, {"error": gerr or "token revoked",
                    "hint": "log in again (POST /accounts/login) for fresh tokens"})
            principal = p["sub"]
            # H5: per-source backstop — G4 caps per principal, but principals are
            # freely mintable pre-personhood; rotation must not defeat flooding
            with LOCK:
                if not _auth_allow("book", _source_of(self)):
                    return self._json(429, {"error": "booking rate limit reached for your source, retry later"})
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
            if listing.get("archived"):  # before any validation: the honest answer is 'archived', not field errors
                return self._json(409, {"error": "listing archived - not bookable"})
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
                    if len(IDEMPOTENCY) >= IDEMPOTENCY_CAP and idem_key not in IDEMPOTENCY:
                        for k in list(IDEMPOTENCY)[:len(IDEMPOTENCY) // 10]:
                            IDEMPOTENCY.pop(k, None)  # FIFO evict oldest 10%
                    prev = IDEMPOTENCY.get(idem_key)
                    if prev:
                        if prev["hash"] != hashlib.sha256(canon.encode()).hexdigest():
                            return self._json(409, {"error": "Idempotency-Key reused with different payload"})
                        return self._json(201, {**prev["response"], "replayed": True})
            with LOCK:  # I3: single reservation section — check + increment atomic under one LOCK
                listing = next((l for l in LISTINGS if l["id"] == lid), None)
                if not listing: return self._json(404, {"error": f"no listing {lid}"})
                if listing.get("archived"):
                    return self._json(409, {"error": "listing archived - not bookable"})
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
            # H5: admin-key brute-force backstop (shared 'admin' kind with vouch)
            with LOCK:
                if not _auth_allow("admin", _source_of(self)):
                    return self._json(429, {"error": "admin rate limit reached for your source, retry later"})
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

    def do_DELETE(self):
        self._t0 = time.time()  # H12: latency start
        # H7: account self-deletion (GDPR-style erasure). The money trail
        # (LEDGER + booking records) survives BY DESIGN — pseudonymous refs,
        # needed for escrow auditability. Owned listings are archived, not
        # destroyed (bookers keep escrow resolution; owner can no longer be
        # relinked because the account is gone).
        path = urlparse(self.path).path
        if int(self.headers.get("Content-Length", 0)) > 65536:
            return self._json(413, {"error": "request body too large (max 64KB)"})
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n)
            data = json.loads(raw) if raw.strip() else {}
        except Exception as ex:
            return self._json(400, {"error": str(ex)})
        if path == "/accounts/me":
            # H5 lesson: count the attempt BEFORE validation — failed deletes
            # count, so brute-forcing the token wall dies at the limit.
            with LOCK:
                if not _auth_allow("delete", _source_of(self)):
                    return self._json(429, {"error": "delete rate limit reached for your source, retry later"})
            cred = self.headers.get("X-Hub-Token", "")
            p, err = None, "missing X-Hub-Token"
            if cred:
                for act in ("list", "book"):
                    p, err = hublib.verify_token(BOOKING_KEY, cred, act, single_use=False)
                    if p: break
            if p:
                p, err = _gen_check(p)
            if not p or not str(p.get("sub", "")).startswith("acct-"):
                return self._json(401, {"error": err or "account token required",
                    "hint": "send a login token as X-Hub-Token"})
            aid = p["sub"]
            confirm = str(data.get("confirm", "")).strip()
            with LOCK:
                if aid not in ACCOUNTS:
                    return self._json(404, {"error": "no account"})
                if confirm != aid:
                    return self._json(400, {"error": "deletion needs typed confirmation: send {\"confirm\": \"<your account id>\"} in the body"})
                n_archived = 0
                for l in LISTINGS:
                    if l.get("owner") == aid and not l.get("archived"):
                        l["archived"] = True
                        n_archived += 1
                del ACCOUNTS[aid]
                LOGIN_CHALLENGES.pop(aid, None)
                _persist_locked()
            return self._json(200, {"ok": True, "account_id": aid,
                "listings_archived": n_archived,
                "note": "account erased (incl. recovery email); owned listings archived; "
                        "the public ledger keeps its pseudonymous refs for escrow auditability; "
                        "every token of this account is now invalid"})
        return self._json(404, {"error": "unknown DELETE route"})

    def log_message(self, *a): pass

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    # H4: single-instance guard. Two hubs sharing one state.json on different
    # ports would interleave writes and corrupt it (the Makefile port guard
    # cannot catch same-state-different-port starts). Take a non-blocking
    # flock on <state>.lock and HOLD the fd for the process lifetime — closing
    # it would release the lock. tools/restore_backup.py probes this same lock
    # before restoring. Exit 79 (78 = corrupt state fail-closed).
    import fcntl
    _H4_LOCK_FD = open(os.path.abspath(STATE_FILE) + ".lock", "a+")
    try:
        fcntl.flock(_H4_LOCK_FD, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print(f"REFUSING to start: state lock is held — another hub is likely "
              f"running on this state ({STATE_FILE}). Stop it first, or if this "
              f"is a stale lock after a crash, remove the .lock file.",
              file=sys.stderr)
        sys.exit(79)
    _load_state()
    print(f"agent-hub-v2 (open/fair) on :{port} - fee {FEE_PCT}%, env {RUN_ENV}, state {STATE_FILE}")
    class HubServer(ThreadingHTTPServer):
        # B6-lesson: default backlog (5) refuses burst connections (B2 caught -1
        # transports at 40 parallel signups). Agentverse/SDK traffic arrives in
        # bursts — a public marketplace hub must queue them instead.
        request_queue_size = 128
        daemon_threads = True

    HubServer(("0.0.0.0", port), Handler).serve_forever()
