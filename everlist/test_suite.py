"""G2 consolidated regression suite for agent-hub-v2.
Runs the full minimum-check list from the master plan against a fresh, self-managed server.
Run: python test_suite.py
"""
import sys
import json, os, re, socket, subprocess, sys, threading, time, hashlib, atexit
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def free_port():
    """G6 lesson: never reuse fixed ports - a stale server from a crashed run
    silently poisons the next run. Allocate a fresh free port every time."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(HERE, "state.json")  # suite uses default state file; cleaned at end

_ACTIVE = []
def _kill_active():
    for p in _ACTIVE:
        try: p.terminate()
        except Exception: pass
atexit.register(_kill_active)  # crashed runs must never leak servers


def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


def start(port, state_file=None, rate=None):
    env = {**os.environ, "HUB_STATE_FILE": state_file or STATE}
    if rate is not None:
        env["HUB_RATE_BOOKS_PER_MIN"] = str(rate)  # G4: functional sections use a raised limit
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    _ACTIVE.append(p)  # atexit guarantee: crashed runs never leak servers
    return p


def stop(p):
    if p:
        p.terminate(); p.wait(timeout=5)


# ================= section 1: manifest + public surface =================
if os.path.exists(STATE): os.remove(STATE)  # deterministic: always start fresh
srv = start(PORT, rate=1000)  # functional sections; G4 has its own dedicated section
assert wait_ready(PORT), "server not ready"
st, man = req("GET", "/.well-known/agent-hub.json")
check("manifest served", st == 200)
check("manifest declares payments rail", "payments" in json.dumps(man))
check("manifest declares identity adapters", "identity" in json.dumps(man))
check("manifest fee is hub-declared", "fee" in json.dumps(man).lower())
st, v = req("GET", "/verticals")
check("verticals listed", st == 200 and "events" in json.dumps(v))

# ================= section 2: access bootstrap + auth negatives =================
st, acc = req("POST", "/access", {"agent": "g2-suite", "acts": ["book", "list"]})
check("access bootstrap", st == 201)
TOK = acc["tokens"]["book"]; LTOK = acc["tokens"]["list"]
st, _ = req("POST", "/book", {"listing_id": "evt-1", "attendee": "x", "quantity": 1, "human_verified": True})
check("auth negative: /book without token 401", st == 401)
st, _ = req("POST", "/book", {"listing_id": "evt-1", "attendee": "x", "quantity": 1, "human_verified": True}, {"X-Hub-Token": "forged"})
check("auth negative: forged token 401", st == 401)
st, _ = req("POST", "/book", {"listing_id": "evt-1", "attendee": "x", "quantity": 1, "human_verified": True}, {"X-Hub-Token": LTOK})
check("auth negative: list-token on /book 403", st == 403)
st, _ = req("POST", "/listings", {"vertical": "events", "title": "t", "date": "2026-10-01", "location": "B", "price": 1, "capacity": 2})
check("auth negative: /listings without token 401", st == 401)
st, _ = req("GET", "/bookings")
check("auth negative: /bookings anonymous 401", st == 401)

# ================= section 3: I1 field injection negatives =================
st, b = req("POST", "/book", {"listing_id": "evt-1", "attendee": "inj", "quantity": 1,
    "human_verified": True, "escrow": "RELEASED", "amount": 0, "hub_fee": 0, "owner_payout": 999}, {"X-Hub-Token": TOK})
check("I1 negative: injected fields rejected 400", st == 400, str(st))
st, b1 = req("POST", "/book", {"listing_id": "evt-1", "attendee": "suite-a", "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK})
check("clean booking created", st == 201)
check("I1: server owns amount", b1["amount"] == 10.0 and b1["escrow"] == "HELD")

# ================= section 4: listing validation =================
st, _ = req("POST", "/listings", {"vertical": "nope", "title": "t"}, {"X-Hub-Token": LTOK})
check("listing negative: unknown vertical 400", st == 400)
st, _ = req("POST", "/listings", {"vertical": "events", "title": "t", "date": "d", "location": "B", "price": "free", "capacity": 2}, {"X-Hub-Token": LTOK})
check("listing negative: bad price 400", st == 400)
st, _ = req("POST", "/listings", {"vertical": "events", "title": "t", "date": "2026-10-01", "location": "B", "price": 5, "capacity": 2, "owner": "o", "registered": 99}, {"X-Hub-Token": LTOK})
check("listing negative: reserved field 400", st == 400)

# ================= section 5: escrow lifecycle incl. invalid transitions =================
st, b2 = req("POST", "/book", {"listing_id": "food-1", "buyer": "suite-b", "quantity": 2, "human_verified": True}, {"X-Hub-Token": TOK})
check("food booking (2x pizza)", st == 201 and b2["amount"] == 17.0)
st, adm = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": b2["id"]}, {"X-Hub-Token": "dev-admin-key-change-me"})
check("admin mint confirm token", st == 201)
st, _ = req("POST", f"/book/{b2['id']}/confirm", {}, {"X-Hub-Token": adm["token"]})
check("escrow: confirm releases", st == 200)
st, _ = req("POST", f"/book/{b2['id']}/confirm", {}, {"X-Hub-Token": adm["token"]})
check("escrow invalid: re-confirm replay 403", st == 403)
st, _ = req("POST", f"/book/{b2['id']}/cancel", {}, {"X-Hub-Token": b2["cancel_token"]})
check("escrow invalid: cancel after release 409", st == 409)
st, _ = req("POST", f"/book/{b1['id']}/cancel", {}, {"X-Hub-Token": b1["cancel_token"]})
check("escrow: cancel refunds HELD booking", st == 200)
st, _ = req("POST", f"/book/{b1['id']}/cancel", {}, {"X-Hub-Token": b1["cancel_token"]})
check("escrow invalid: cancel replay 403 (single-use)", st == 403)

# ================= section 6: I4 idempotency =================
st, i1 = req("POST", "/book", {"listing_id": "evt-2", "attendee": "idem", "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK, "Idempotency-Key": "suite-idem-1"})
st2, i2 = req("POST", "/book", {"listing_id": "evt-2", "attendee": "idem", "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK, "Idempotency-Key": "suite-idem-1"})
check("idempotency: same key+payload replays same id", st == st2 == 201 and i1["id"] == i2["id"] and i2.get("replayed") is True)
st3, _ = req("POST", "/book", {"listing_id": "evt-2", "attendee": "idem", "quantity": 2, "human_verified": True}, {"X-Hub-Token": TOK, "Idempotency-Key": "suite-idem-1"})
check("idempotency: same key different payload 409", st3 == 409)

# ================= section 7: I3 concurrency last-seat + cancel-restore =================
st, tiny = req("POST", "/listings", {"vertical": "events", "title": "Tiny", "date": "2026-10-05", "location": "B", "price": 5, "capacity": 3}, {"X-Hub-Token": LTOK})
lid = tiny["id"]
results = []
def racer(i):
    s, r = req("POST", "/book", {"listing_id": lid, "attendee": f"r{i}", "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK})
    results.append(s)
threads = [threading.Thread(target=racer, args=(i,)) for i in range(12)]
[t.start() for t in threads]; [t.join() for t in threads]
ok = sum(1 for s in results if s == 201); rej = sum(1 for s in results if s == 409)
check("I3: last-seat race exact", ok == 3 and rej == 9, f"ok={ok} rej={rej}")
st, ls = req("GET", "/listings?vertical=events")
tiny_now = next(l for l in ls["listings"] if l["id"] == lid)
check("I3: no overbooking", tiny_now["registered"] == 3)

# ================= section 8: I5 pseudonymity projections =================
name = "Suite-Privacy-Person"
derived = "anon-" + hashlib.sha256(name.encode()).hexdigest()[:12]
st, p1 = req("POST", "/book", {"listing_id": "evt-1", "attendee": name, "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK})
st, p2 = req("POST", "/book", {"listing_id": "evt-2", "attendee": name, "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK})
check("I5: refs not name-derived", derived not in json.dumps([p1, p2]))
check("I5: same person unlinked across bookings", p1["attendee"] != p2["attendee"])
st, bk = req("GET", "/bookings", headers={"X-Hub-Token": TOK})
check("I5: no raw names in principal-scoped bookings", name not in json.dumps(bk))
st, pv = req("GET", f"/book/{p1['id']}", headers={"X-Hub-Token": p1["booking_secret"]})
check("I5: private store returns real name with secret", st == 200 and pv["private_details"]["attendee"] == name)
st, _ = req("GET", f"/book/{p1['id']}?secret={p1['booking_secret']}")
check("H1: query-string secret rejected 403", st == 403)

# ================= section 9: G5 token vectors =================
st, _ = req("GET", f"/book/{p1['id']}", headers={"X-Hub-Token": p2["booking_secret"]})
check("G5: wrong-booking secret 403", st == 403)
st, _ = req("POST", f"/book/{p1['id']}/cancel", {}, {"X-Hub-Token": p2["cancel_token"]})
check("G5: cancel token bound to other booking 403", st == 403)

# ================= section 10: G1 restart persistence + crash-injection =================
stop(srv)
time.sleep(0.5)
srv2 = start(PORT + 1)  # same STATE file (default), different port
check("G1: server restarts", wait_ready(PORT + 1))
BASE2 = f"http://127.0.0.1:{PORT+1}"
import urllib.request as _u
def req2(method, path, body=None, headers=None):
    r = _u.Request(BASE2 + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with _u.urlopen(r, timeout=10) as resp: return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
st, acc2 = req2("POST", "/access", {"agent": "g2-suite", "acts": ["book"]})
check("G1: access works after restart", st == 201)
st, bk2 = req2("GET", "/bookings", headers={"X-Hub-Token": acc2["tokens"]["book"]})
check("G1: bookings survived restart", st == 200 and len(bk2["bookings"]) >= 5)
suite_bookings = bk2["bookings"]
check("G1: escrow states preserved", any(b["escrow"] == "RELEASED" for b in suite_bookings))
raw_state = open(STATE).read()
check("G1: no real names persisted", name not in raw_state)
st3, i3 = req2("POST", "/book", {"listing_id": "evt-2", "attendee": "idem", "quantity": 1, "human_verified": True}, {"X-Hub-Token": TOK, "Idempotency-Key": "suite-idem-1"})
check("G1: idempotency replays across restart", st3 == 201 and i3["id"] == i1["id"] and i3.get("replayed") is True)

# ================= section 11: G4 rate limiting (dedicated default-limit server) =================
STATE_G4 = STATE + ".g4"
if os.path.exists(STATE_G4): os.remove(STATE_G4)
srv3 = start(PORT + 2, state_file=STATE_G4)  # default rate limit: 10 books/min
BASE3 = f"http://127.0.0.1:{PORT+2}"
def req3(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE3 + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp: return resp.status, json.loads(resp.read().decode()), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode()), dict(e.headers)
        except Exception: return e.code, {}, dict(e.headers)
check("G4: rate-limit server up", wait_ready(PORT + 2))
st, a3, _ = req3("POST", "/access", {"agent": "g4-principal", "acts": ["book"]})
codes = []
for i in range(12):
    st, r3, h3 = req3("POST", "/book", {"listing_id": "evt-1", "attendee": f"g4-{i}", "quantity": 1, "human_verified": True}, {"X-Hub-Token": a3["tokens"]["book"]})
    codes.append(st)
check("G4: 12 rapid books -> last 2 get 429", codes[:10].count(201) == 10 and codes[10:] == [429, 429], str(codes))
st, r3, h3 = req3("POST", "/book", {"listing_id": "evt-1", "attendee": "renamed-but-same-principal", "quantity": 1, "human_verified": True}, {"X-Hub-Token": a3["tokens"]["book"]})
check("G4: rename does not evade principal limit", st == 429 and int(h3.get("Retry-After", 0)) > 0)
st413 = None
try:
    big = urllib.request.Request(BASE3 + "/book", method="POST", data=("x" * 70000).encode(),
        headers={"Content-Type": "application/json", "X-Hub-Token": a3["tokens"]["book"]})
    urllib.request.urlopen(big, timeout=5)
except urllib.error.HTTPError as e:
    st413 = e.code
except Exception:
    st413 = None
check("G4: oversized body 413", st413 == 413, str(st413))
stop(srv3); srv3 = None
if os.path.exists(STATE_G4): os.remove(STATE_G4)

# ================= section 12: merchant orders (E1/E2 gap fix) =================
# second merchant with its own listing
st, acc2 = req2("POST", "/access", {"agent": "merchant-b", "acts": ["book", "list"]})
check("orders: merchant B bootstrap", st == 201)
TOKB, LTOKB = acc2["tokens"]["book"], acc2["tokens"]["list"]
st, lb = req2("POST", "/listings", {"vertical": "events", "title": "B-Event",
    "date": "2026-11-01", "location": "C", "price": 3, "capacity": 10}, {"X-Hub-Token": LTOKB})
check("orders: merchant B listing created", st == 201)
# anti-spoof: owner field from client must be rejected even with a valid list token
st, _ = req2("POST", "/listings", {"vertical": "events", "title": "Sneak", "date": "2026-11-02",
    "location": "C", "price": 1, "capacity": 5, "owner": "g2-suite"}, {"X-Hub-Token": LTOKB})
check("orders: owner spoof rejected 400", st == 400)
st, lbj = req2("GET", "/listings?vertical=events")
bid_owner = next(l for l in lbj["listings"] if l["id"] == lb["id"])["owner"]
check("orders: listing owner == authenticated principal", bid_owner == "merchant-b")
# buyer books on BOTH merchants' listings
st, ob = req2("POST", "/book", {"listing_id": lb["id"], "attendee": "Charlie", "quantity": 1,
    "human_verified": True}, {"X-Hub-Token": TOK})
check("orders: booking on merchant B listing", st == 201)
booked_id = ob["id"]
# merchant B sees exactly its own order; suite merchant (g2-suite) sees its own too
st, ordb = req2("GET", "/orders", None, {"X-Hub-Token": LTOKB})
check("orders: merchant B /orders 200", st == 200)
b_ids = {o["id"] for o in ordb["orders"]}
check("orders: merchant B sees own incoming order", booked_id in b_ids)
check("orders: merchant B does NOT see other merchants' bookings",
      all(o["listing_id"] == lb["id"] for o in ordb["orders"]))
pub_names = [o.get("attendee", "") for o in ordb["orders"]]
check("orders: pseudonymous refs only (no real names)", all(not n or not n.isalpha() for n in pub_names))
st, orda = req2("GET", "/orders", None, {"X-Hub-Token": LTOK})
check("orders: merchant A sees own bookings", st == 200 and len(orda["orders"]) >= 1)
check("orders: merchant A does not see merchant B order", booked_id not in {o["id"] for o in orda["orders"]})
st, _ = req2("GET", "/orders")
check("orders: anonymous 401", st == 401)

# ================= summary =================
stop(srv2)
if os.path.exists(STATE): os.remove(STATE)
failed = [n for n, ok in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:", failed); sys.exit(1)
print("G2_SUITE_ALL_PASSED")
