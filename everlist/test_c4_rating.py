"""C4: reputation — buyers rate SETTLED bookings once; listings show aggregates.

  auth:        no token 401, garbage token 403, wrong-buyer token 403 (owner-immutable)
  validation:  0/6/'5'/bool/missing -> 400 strict integer [1,5]
  gate:        HELD -> 409 (not settled); RELEASED -> 200; WAIVED (free) -> 200;
               REFUNDED -> 409 (cancelled bookings are not successes)
  once-only:   second rating of the same booking -> 409 'already rated'
  aggregates:  rating_sum/rating_count on the listing, avg math exact,
               visible on public surfaces (single fetch + search)
  anonymity:   LEDGER untouched by ratings (no entries, no identity data)
  chat:        usage text, success + aggregate echo, double-rate rejection,
               non-buyer rejection, anonymous agent-identity path
Run: python test_c4_rating.py
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
    tmp = tempfile.mkdtemp(prefix="hub-c4-")
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
    logf = open(os.path.join(tmp, "hub.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


HUB, _p, _log = spawn()
ADMIN = "dev-admin-key-change-me"  # documented dev default (HUB_ADMIN_KEY env)


def tok(agent, act):
    st, res = req("POST", "/access", {"agent": agent, "acts": [act]})
    assert st in (200, 201), f"access failed: {st} {res}"  # 201 Created (H17 contract)
    return res["tokens"][act]


def book(buyer_tok, lid, name):
    st, res = req("POST", "/book", {"listing_id": lid, "attendee": name,
                                    "human_verified": True}, headers={"X-Hub-Token": buyer_tok})
    assert st == 201, f"book failed: {st} {res}"
    return res["id"]


def confirm(bid):
    st, res = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid},
                  headers={"X-Hub-Token": ADMIN})
    assert st in (200, 201), f"confirm-token mint failed: {st} {res}"
    st, res2 = req("POST", f"/book/{bid}/confirm", {}, headers={"X-Hub-Token": res["token"]})
    assert st == 200, f"confirm failed: {st} {res2}"


# ---- setup: paid listing + free listing, owner + two buyers
OWN = tok("c4-owner", "list")
A = tok("c4-buyer-a", "book")
B = tok("c4-buyer-b", "book")

st, res = req("POST", "/listings", {"vertical": "events", "title": "C4Suite Gala",
                                    "category": "party", "date": "2026-12-05",
                                    "price": 5, "location": "Vienna", "capacity": 10},
              headers={"X-Hub-Token": OWN})
assert st == 201, f"listing failed: {res}"
LID = res["id"]

st, res = req("POST", "/listings", {"vertical": "events", "title": "C4Suite Free Class",
                                    "category": "community", "date": "2026-12-06",
                                    "price": 0, "location": "Vienna", "capacity": 10},
              headers={"X-Hub-Token": OWN})
assert st == 201, f"free listing failed: {res}"
FREE = res["id"]

# ---- validation + auth (before any settlement exists)
check("C4 rate no token -> 401", req("POST", "/book/unknown/rate", {"rating": 5})[0] == 401)
check("C4 rate garbage token -> 403",
      req("POST", "/book/unknown/rate", {"rating": 5},
          headers={"X-Hub-Token": "junk.token"})[0] == 403)

BK1 = book(A, LID, "Alice")  # stays HELD for the gate test
BK2 = book(B, LID, "Bob")

# ---- strict validation (H17-style: booleans/strings/floats rejected)
for bad in (0, 6, "5", True, None):
    body = {} if bad is None else {"rating": bad}
    st, res = req("POST", f"/book/{BK1}/rate", body, headers={"X-Hub-Token": A})
    check(f"C4 rating {bad!r} -> 400 strict", st == 400, f"{st} {res}")

# ---- settlement gate: HELD refuses, RELEASED accepts
st, res = req("POST", f"/book/{BK1}/rate", {"rating": 5}, headers={"X-Hub-Token": A})
check("C4 rate while HELD -> 409 (not settled)",
      st == 409 and "settlement" in res.get("error", ""), f"{st} {res}")

confirm(BK2)
st, res = req("POST", f"/book/{BK2}/rate", {"rating": 5}, headers={"X-Hub-Token": B})
check("C4 rate after RELEASED -> 200", st == 200 and res.get("rating") == 5, f"{st} {res}")
check("C4 aggregate echoes avg", "5.0" in res.get("aggregate", ""), res.get("aggregate", ""))

# ---- once-only
st, res = req("POST", f"/book/{BK2}/rate", {"rating": 1}, headers={"X-Hub-Token": B})
check("C4 second rating -> 409 already rated",
      st == 409 and "already rated" in res.get("error", ""), f"{st} {res}")

# ---- owner-immutable: owner's list token cannot rate someone's booking
OWN_BOOK = tok("c4-owner", "book")  # even with a book-act token, principal != buyer
st, res = req("POST", f"/book/{BK2}/rate", {"rating": 5}, headers={"X-Hub-Token": OWN_BOOK})
check("C4 non-buyer principal -> 403 (owner-immutable)",
      st == 403 and "buyer" in res.get("error", ""), f"{st} {res}")

# ---- WAIVED (free) bookings rateable; REFUNDED not
FBK = book(A, FREE, "Carol")
st, res = req("POST", f"/book/{FBK}/rate", {"rating": 4}, headers={"X-Hub-Token": A})
check("C4 WAIVED (free) booking rateable -> 200", st == 200 and res.get("rating") == 4, f"{st} {res}")

st, cbres = req("POST", "/book", {"listing_id": FREE, "attendee": "Dave", "human_verified": True},
                headers={"X-Hub-Token": B})
CBK = cbres["id"]
st, res = req("POST", f"/book/{CBK}/cancel", {}, headers={"X-Hub-Token": cbres["cancel_token"]})
check("C4 setup: cancel with cancel_token works (REFUNDED)",
      st == 200 and res.get("escrow") == "REFUNDED", f"{st} {res}")
st, res = req("POST", f"/book/{CBK}/rate", {"rating": 5}, headers={"X-Hub-Token": B})
check("C4 REFUNDED booking -> 409 (not a success)", st == 409, f"{st} {res}")

# ---- aggregate math on the PUBLIC listing (Gala: one 5-star rating)
st, l = req("GET", f"/listings/{LID}")
check("C4 public listing shows rating_sum/count", st == 200
      and l.get("rating_sum") == 5 and l.get("rating_count") == 1,
      f"{st} sum={l.get('rating_sum')} count={l.get('rating_count')}")

# second buyer on Gala: confirm + rate 4 -> avg 4.5
BK3 = book(A, LID, "Eve")
confirm(BK3)
st, res = req("POST", f"/book/{BK3}/rate", {"rating": 4}, headers={"X-Hub-Token": A})
check("C4 aggregate math avg 4.5 (2 ratings)", st == 200 and "4.5" in res.get("aggregate", "")
      and "2 rating" in res.get("aggregate", ""), res.get("aggregate", ""))
st, l = req("GET", f"/listings/{LID}")
check("C4 listing aggregates updated (sum 9, count 2)",
      l.get("rating_sum") == 9 and l.get("rating_count") == 2, f"{l.get('rating_sum')},{l.get('rating_count')}")

# search surface carries aggregates too
st, res = req("GET", f"/search?q=c4suite&category=party")
gala = next((x for x in res.get("listings", []) if x["id"] == LID), {})
check("C4 search results carry aggregates", gala.get("rating_count") == 2, str(gala.get("rating_count")))

# ---- ledger anonymity: ratings add NO ledger entries
BK4 = book(B, FREE, "Fay")
st, led = req("GET", "/ledger")
n_before = len(led.get("ledger", []))  # snapshot AFTER the booking (bookings DO ledger; ratings must not)
req("POST", f"/book/{BK4}/rate", {"rating": 3}, headers={"X-Hub-Token": B})
st, led2 = req("GET", "/ledger")
check("C4 ledger untouched by ratings (no entries, no identity data)",
      len(led2.get("ledger", [])) == n_before and "Fay" not in json.dumps(led2),
      f"before={n_before} after={len(led2.get('ledger', []))}")

# ---- OpenAPI documents the rating endpoint
st, api = req("GET", "/openapi.json")
rate_op = api.get("paths", {}).get("/book/{id}/rate", {}).get("post", {})
check("C4 OpenAPI documents /book/{id}/rate", st == 200 and bool(rate_op), str(rate_op)[:80])

# ---- chat paths (real chatlib)
import chatlib  # noqa: E402

r = chatlib.handle_text(HUB, "rate", "c4-chat-x")
check("C4 chat bare 'rate' -> usage", "Usage: rate" in r, r[:80])

# anonymous chat: stranger chat (its own agent principal) cannot rate someone else's booking
st, res = req("POST", "/book", {"listing_id": FREE, "attendee": "Ivy", "human_verified": True},
              headers={"X-Hub-Token": tok("c4-ivy", "book")})
IVYBK = res["id"]
r = chatlib.handle_text(HUB, f"rate {IVYBK} 4", "c4-not-the-buyer")
check("C4 chat rate by non-buyer rejected", "Only the booking's buyer" in r, r[:100])

# anonymous chat with the buyer's own principal
st, res = req("POST", "/book", {"listing_id": FREE, "attendee": "Gina", "human_verified": True},
              headers={"X-Hub-Token": tok("c4-chat-buyer", "book")})
CHATBK = res["id"]
r = chatlib.handle_text(HUB, f"rate {CHATBK} 5", "c4-chat-buyer")
check("C4 chat anonymous rate success + aggregate", "Thanks" in r and "5/5" in r, r[:100])
r = chatlib.handle_text(HUB, f"rate {CHATBK} 5", "c4-chat-buyer")
check("C4 chat double-rate -> already rated", "already rated" in r, r[:100])

# logged-in session path
sess = {"account_id": "acct-c4demo", "tokens": {"book": tok("c4-buyer-a", "book")},
        "human_verified": False}
chatlib._set_session("c4-logged-in", sess)
st2, res2 = req("POST", "/book", {"listing_id": FREE, "attendee": "Hank", "human_verified": True},
                headers={"X-Hub-Token": sess["tokens"]["book"]})
r = chatlib.handle_text(HUB, f"rate {res2['id']} 2", "c4-logged-in")
check("C4 chat logged-in rate uses session token", "Thanks" in r and "2/5" in r, r[:100])

print(f"\n=== C4 rating: {sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} passed ===")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("C4_RATING_PASSED")
