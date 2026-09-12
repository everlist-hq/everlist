"""C12: payment terms (SPEC section 19, owner-pinned).

Covered here:
- listing payment_terms: vertical defaults injected when omitted (escrow,
  72h events/services, 168h food), full validation walls (unknown keys,
  bad rail, window bounds, instant+window contradiction, deposit > price)
- booking consent: default-terms bookings stay friction-free (no echo);
  custom-terms paid bookings REQUIRE the exact accepted_payment_terms echo
  (409 carries the terms); free listings are exempt
- instant rail: booking settles at booking time (escrow DIRECT), confirm
  and cancel refuse honestly, rating opens, escrow_ref is a contradiction
- server-side terms snapshot on every booking (later edits never rewrite)
- owner-editable terms via manage (pre-booking), null resets to default
- legacy migration: listings persisted before C12 get defaults at load

Run: python test_c12_payment_terms.py
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


def post(base, path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Hub-Token"] = token
    rq = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                method="POST", headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def get(base, path, token=None):
    headers = {}
    if token:
        headers["X-Hub-Token"] = token
    rq = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def spawn(state_file, extra_env=None):
    port = free_port()
    logdir = os.path.dirname(state_file)
    env = {**os.environ, "HUB_STATE_FILE": state_file,
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1", **(extra_env or {})}
    logf = open(os.path.join(logdir, "hub-c12.log"), "a")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


def tokens(base, agent, acts):
    _, r = post(base, "/access", {"agent": agent, "acts": acts})
    return r.get("tokens", {})


TMP = tempfile.mkdtemp(prefix="hub-c12-")
STATE = os.path.join(TMP, "state.json")
HUB, _p, _log = spawn(STATE)
LT = tokens(HUB, "c12-merchant", ["list"])["list"]
BT = tokens(HUB, "c12-buyer", ["book"])["book"]


def mk(price, terms=None, vert="events"):
    body = {"vertical": vert, "title": f"C12 {time.time()} {price}", "price": price,
            "location": "Vienna", "capacity": 10, "date": "2026-12-01"}
    if terms is not None:
        body["payment_terms"] = terms
    st, r = post(HUB, "/listings", body, token=LT)
    assert st == 201, f"seed failed: {r}"
    return r["id"], r.get("manage_code", "")


def book(lid, extra=None, token=BT):
    body = {"listing_id": lid, "attendee": "C12 Buyer", "human_verified": True}
    body.update(extra or {})
    return post(HUB, "/book", body, token=token)


# --- H1: defaults injected at creation ---------------------------------
LID_DEF, CODE_DEF = mk(10)
st, lst = get(HUB, f"/listings/{LID_DEF}")
check("H1 default terms injected (escrow/72h/0)",
      st == 200 and lst.get("payment_terms") ==
      {"rail": "escrow", "refund_window_hours": 72, "deposit_required": 0.0},
      f"got {lst.get('payment_terms') if st == 200 else st}")

# --- H2/H3: explicit terms stored verbatim -----------------------------
LID_INST, CODE_INST = mk(10, {"rail": "instant"})
st, lst = get(HUB, f"/listings/{LID_INST}")
INST_TERMS = {"rail": "instant", "refund_window_hours": 0, "deposit_required": 0.0}
check("H2 instant terms stored + normalized",
      st == 200 and lst.get("payment_terms") == INST_TERMS,
      f"got {lst.get('payment_terms') if st == 200 else st}")

LID_W48, _ = mk(10, {"refund_window_hours": 48, "deposit_required": 2.0})
st, lst = get(HUB, f"/listings/{LID_W48}")
W48_TERMS = {"rail": "escrow", "refund_window_hours": 48, "deposit_required": 2.0}
check("H3 custom window + deposit stored",
      st == 200 and lst.get("payment_terms") == W48_TERMS,
      f"got {lst.get('payment_terms') if st == 200 else st}")

# --- H4: validation walls ----------------------------------------------
for name, terms in (("bad rail", {"rail": "banana"}),
                    ("window 0 on escrow", {"refund_window_hours": 0}),
                    ("window over max", {"refund_window_hours": 1000}),
                    ("instant + window", {"rail": "instant", "refund_window_hours": 24}),
                    ("deposit above price", {"deposit_required": 99}),
                    ("unknown key", {"rail": "escrow", "curve": "secp"})):
    st, r = post(HUB, "/listings", {"vertical": "events", "title": "C12 bad",
        "price": 10, "location": "Vienna", "capacity": 10,
        "date": "2026-12-01", "payment_terms": terms}, token=LT)
    check(f"H4 reject: {name}", st == 400, f"got {st}: {r}")

# --- H5: default terms book friction-free (no echo) ---------------------
st, r = book(LID_DEF)
default_bid = r.get("id", "")
check("H5 default-terms paid booking needs no echo",
      st == 201 and r.get("escrow") == "HELD", f"got {st}: {r}")

# --- H6: custom terms without echo -> 409 carrying the terms ------------
st, r = book(LID_INST)
check("H6 instant booking without echo -> 409 + terms in error",
      st == 409 and r.get("payment_terms") == INST_TERMS, f"got {st}: {r}")

# --- H7: wrong echo -> 409 ----------------------------------------------
st, r = book(LID_INST, {"accepted_payment_terms": {"rail": "instant"}})
check("H7 wrong echo -> 409", st == 409, f"got {st}: {r}")

# --- H8: exact echo -> 201 DIRECT + snapshot ----------------------------
st, r = book(LID_INST, {"accepted_payment_terms": INST_TERMS})
inst_bid = r.get("id", "")
inst_cancel = r.get("cancel_token", "")
check("H8 exact echo -> 201, escrow DIRECT, terms snapshotted",
      st == 201 and r.get("escrow") == "DIRECT" and r.get("payment_terms") == INST_TERMS,
      f"got {st}: {r}")

# --- H9: DIRECT confirm/cancel refuse honestly ---------------------------
st_c, r_c = post(HUB, f"/book/{inst_bid}/cancel", {"x": 1}, token=inst_cancel)
check("H9 DIRECT cancel refuses (no refund window)",
      st_c == 409 and "instant" in str(r_c.get("error", "")), f"got {st_c}: {r_c}")
st_cf, r_cf = post(HUB, f"/book/{inst_bid}/confirm", {}, token="c12-not-a-real-admin-token")
check("H9b confirm without admin token never releases",
      st_cf in (401, 403), f"got {st_cf}: {r_cf}")

# --- H10: rating opens for DIRECT (settled at booking) -------------------
st, r = post(HUB, f"/book/{inst_bid}/rate", {"rating": 5}, token=BT)
check("H10 DIRECT booking is rateable", st == 200, f"got {st}: {r}")

# --- H11: free listings stay exempt (no echo, WAIVED) -------------------
LID_FREE, _ = mk(0, {"rail": "instant"})
st, r = book(LID_FREE)
check("H11 free + custom terms books WAIVED without echo",
      st == 201 and r.get("escrow") == "WAIVED", f"got {st}: {r}")

# --- H12: instant + escrow_ref is a contradiction ------------------------
st, r = book(LID_INST, {"accepted_payment_terms": INST_TERMS,
                        "escrow_ref": {"contract": "0xabc", "escrow_id": 1, "tx": "0xtx"}})
check("H12 instant + escrow_ref -> 409", st == 409 and "escrow_ref" in str(r.get("error", "")),
      f"got {st}: {r}")

# --- H13: owner-editable terms (manage), snapshot protection -------------
st, r = post(HUB, f"/listings/{LID_DEF}/manage",
             {"manage_code": CODE_DEF, "action": "edit",
              "payment_terms": {"refund_window_hours": 48, "deposit_required": 1.0}}, token=LT)
check("H13a terms edit accepted", st == 200, f"got {st}: {r}")
EDITED = {"rail": "escrow", "refund_window_hours": 48, "deposit_required": 1.0}
st, r = book(LID_DEF)
check("H13b old-terms booking now needs echo", st == 409 and r.get("payment_terms") == EDITED,
      f"got {st}: {r}")
st, r = book(LID_DEF, {"accepted_payment_terms": EDITED})
check("H13c new-terms echo books, escrow HELD",
      st == 201 and r.get("escrow") == "HELD" and r.get("payment_terms") == EDITED,
      f"got {st}: {r}")
# earlier H5 booking keeps its ORIGINAL snapshot
st, r = get(HUB, f"/bookings/{default_bid}", token=BT)
check("H13d existing booking snapshot untouched by edit",
      st == 200 and r.get("payment_terms") ==
      {"rail": "escrow", "refund_window_hours": 72, "deposit_required": 0.0},
      f"got {st}: {r}")
# null resets to the vertical default
st, r = post(HUB, f"/listings/{LID_DEF}/manage",
             {"manage_code": CODE_DEF, "action": "edit", "payment_terms": None}, token=LT)
check("H13e terms reset to default", st == 200, f"got {st}: {r}")
st, r = book(LID_DEF)
check("H13f post-reset booking friction-free again", st == 201, f"got {st}: {r}")

# --- H14: legacy migration at load ---------------------------------------
_kill(_p); _ACTIVE.clear()
time.sleep(0.5)
with open(STATE) as fh:
    snap = json.load(fh)
stripped = 0
for l in snap.get("listings", []):
    if l.get("payment_terms"):
        del l["payment_terms"]; stripped += 1
assert stripped >= 4, f"expected seeded listings in state, found {stripped}"
with open(STATE, "w") as fh:
    json.dump(snap, fh)
HUB, _p2, _log = spawn(STATE)
st, lst = get(HUB, f"/listings/{LID_W48}")
check("H14 legacy listing gets default terms at load",
      st == 200 and lst.get("payment_terms") ==
      {"rail": "escrow", "refund_window_hours": 72, "deposit_required": 0.0},
      f"got {lst.get('payment_terms') if st == 200 else st}")

print()
failed = [n for n, ok in RESULTS if not ok]
print(f"{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
sys.exit(1 if failed else 0)
