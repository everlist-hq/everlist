"""C3: my-bookings — principal-scoped booking visibility, end to end.

The hub's GET /bookings (I2) is ALREADY principal-scoped; C3 reuses it
(established paths) and adds: chat 'my-bookings' command + this suite:
  hub auth matrix:   no token 401, garbage token 401, book token 200
  isolation:         buyer A never sees B's bookings (and vice versa)
  merchant view:     /orders shows only bookings on MY listings,
                     buyer names stay pseudonymous in the payload
  SDK parity:        bookings() / orders() match the raw API
  chat:              'my-bookings' anonymous (agent identity), logged-in
                     (session), empty-state wording, 'my bookings' variant
Run: python test_c3_bookings.py
"""
import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sdk"))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


_ACTIVE = []
atexit.register(lambda: [_kill(p) for p in _ACTIVE])


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


def req(method, path, body=None, headers=None, base=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request((base or HUB) + path,
                               data=(json.dumps(body).encode() if body is not None else None),
                               method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def spawn():
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hub-c3-")
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
    logf = open(os.path.join(tmp, "hub.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


HUB, _p, _log = spawn()

# ---- principals: two buyers, one owner, one second owner (isolation witness)
def tok(agent, act):
    st, res = req("POST", "/access", {"agent": agent, "acts": [act]})
    assert st in (200, 201), f"access failed: {st} {res}"  # /access answers 201 Created (H17 contract)
    return res["tokens"][act]


A = tok("c3-buyer-a", "book")
B = tok("c3-buyer-b", "book")
OWN = tok("c3-owner", "list")
OWN2 = tok("c3-owner2", "list")

# owner listing (paid so escrow HELD; unique word keeps demo listings out)
st, res = req("POST", "/listings", {"vertical": "events", "title": "C3Suite Gala",
                                    "category": "party", "date": "2026-11-05",
                                    "price": 5, "location": "Vienna", "capacity": 10},
              headers={"X-Hub-Token": OWN})
check("C3 owner listing created", st == 201 and res.get("id", "").startswith("even-"), f"{st} {res}")
LID = res["id"]

# ---- hub auth matrix on GET /bookings
check("C3 /bookings no token -> 401", req("GET", "/bookings")[0] == 401)
check("C3 /bookings garbage token -> 401",
      req("GET", "/bookings", headers={"X-Hub-Token": "junk.token.here"})[0] == 401)

# ---- buyers book (paid -> HELD)
st, r1 = req("POST", "/book", {"listing_id": LID, "attendee": "Alice Buyer",
                                "human_verified": True}, headers={"X-Hub-Token": A})
check("C3 buyer A books (HELD)", st == 201 and r1.get("escrow") == "HELD", f"{st} {r1.get('escrow')}")
BK1 = r1["id"]
st, r2 = req("POST", "/book", {"listing_id": LID, "attendee": "Bob Buyer",
                                "human_verified": True}, headers={"X-Hub-Token": B})
check("C3 buyer B books", st == 201, f"{st}")
BK2 = r2["id"]

# ---- principal isolation (the core of C3)
st, ra = req("GET", "/bookings", headers={"X-Hub-Token": A})
ids_a = [b["id"] for b in ra.get("bookings", [])]
check("C3 A sees exactly own booking", st == 200 and ids_a == [BK1], f"{st} {ids_a}")
st, rb = req("GET", "/bookings", headers={"X-Hub-Token": B})
ids_b = [b["id"] for b in rb.get("bookings", [])]
check("C3 B sees exactly own booking (not A's)", st == 200 and ids_b == [BK2], f"{st} {ids_b}")
check("C3 booking payload carries escrow + amount",
      ra["bookings"][0].get("escrow") == "HELD" and ra["bookings"][0].get("amount") == 5.0,
      str(ra["bookings"][0])[:80])

# ---- list act token also accepted on /bookings (I2 dual-act design)
st, rl = req("GET", "/bookings", headers={"X-Hub-Token": tok("c3-buyer-a", "list")})
check("C3 list-act token accepted for own bookings",
      st == 200 and [b["id"] for b in rl.get("bookings", [])] == [BK1], f"{st}")

# ---- merchant view /orders: owner sees own-listing bookings, pseudonymized
st, od = req("GET", "/orders", headers={"X-Hub-Token": OWN})
oids = [b["id"] for b in od.get("orders", [])]
check("C3 owner sees bookings on own listing", st == 200 and set(oids) == {BK1, BK2}, f"{st} {oids}")
check("C3 orders pseudonymous: real buyer names absent",
      "Alice Buyer" not in json.dumps(od) and "Bob Buyer" not in json.dumps(od))
st, od2 = req("GET", "/orders", headers={"X-Hub-Token": OWN2})
check("C3 second owner sees zero orders (isolation)", st == 200 and od2.get("orders") == [])

# ---- SDK parity
from agenthub.client import AgentHub  # noqa: E402

cl = AgentHub(HUB)
cl.bootstrap("c3-buyer-a", acts=("book", "list"))
sdk_b = [b.id for b in cl.bookings()]
check("C3 SDK bookings() == raw API for same principal", sdk_b == [BK1], f"{sdk_b}")
cl2 = AgentHub(HUB)
cl2.bootstrap("c3-owner", acts=("book", "list"))
sdk_o = [b.id for b in cl2.orders()]
check("C3 SDK orders() == merchant view", set(sdk_o) == {BK1, BK2}, f"{sdk_o}")

# ---- chat: my-bookings (real chatlib, three paths)
import chatlib  # noqa: E402

# 1. anonymous: this chat's agent address is its principal -> empty state
r = chatlib.handle_text(HUB, "my-bookings", "c3-anon-chat-agent")
check("C3 chat anonymous my-bookings -> honest empty state",
      "no bookings yet" in r.lower(), r[:100])

# 2. anonymous principal WITH a booking: same address as buyer A via /access
r = chatlib.handle_text(HUB, "my-bookings", "c3-buyer-a")
check("C3 chat anonymous sees its principal's booking",
      "Your bookings (1)" in r and BK1 in r and "HELD" in r, r[:120])

# 3. space variant routes to the same intent
r = chatlib.handle_text(HUB, "my bookings", "c3-buyer-a")
check("C3 chat 'my bookings' variant routes", "Your bookings (1)" in r, r[:80])

# 4. logged-in session: session token proves the account
sess_acc = {"account_id": "acct-c3demo", "tokens": {"book": A}, "human_verified": False}
chatlib._set_session("c3-logged-in", sess_acc)
r = chatlib.handle_text(HUB, "my-bookings", "c3-logged-in")
check("C3 chat logged-in my-bookings uses session token",
      "Your bookings (1)" in r and BK1 in r, r[:120])

print(f"\n=== C3 my-bookings: {sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} passed ===")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("C3_BOOKINGS_PASSED")
