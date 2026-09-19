"""S7: refund-window enforcement on /cancel (owner GO 2026-09-18).

Covered here:
- dynamic cancel_token TTL: TTL >= refund_window + 48h (was fixed 7d —
  a 30-day-window buyer lost their self-refund credential at day 7)
- in-window cancel: HELD -> REFUNDED 200 (positive path intact)
- aged booking (window expired): cancel -> 409 with the honest chain note
  (the hub must never bookkeep a refund the contract will not execute;
  sync-escrow refuses the divergent hub REFUNDED / chain RELEASED pair)
- WAIVED (free) bookings stay cancellable when old (no money moves)

Run: python test_cancel_window.py
"""
import atexit
import base64
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
    logf = open(os.path.join(logdir, "hub-cancel.log"), "a")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


def tokens(base, agent, acts):
    _, r = post(base, "/access", {"agent": agent, "acts": acts})
    return r.get("tokens", {})


def tok_ttl(token):
    body = token.split(".", 1)[0]
    payload = json.loads(base64.urlsafe_b64decode(body + "==").decode())
    return payload["exp"] - int(time.time())


TMP = tempfile.mkdtemp(prefix="hub-cancel-")
STATE = os.path.join(TMP, "state.json")
HUB, _p, _log = spawn(STATE)
LT = tokens(HUB, "cancel-merchant", ["list"])["list"]
BT = tokens(HUB, "cancel-buyer", ["book"])["book"]


def mk(price, terms=None, vert="events"):
    body = {"vertical": vert, "title": f"S7 {time.time()} {price}", "price": price,
            "location": "Vienna", "capacity": 10, "date": "2026-12-01"}
    if terms is not None:
        body["payment_terms"] = terms
    st, r = post(HUB, "/listings", body, token=LT)
    assert st == 201, f"seed failed: {r}"
    return r["id"]


def book(lid, token=BT, extra=None):
    body = {"listing_id": lid, "attendee": "S7 Buyer",
            "human_verified": True}
    body.update(extra or {})
    return post(HUB, "/book", body, token=token)


# --- T1: dynamic TTL outlives a 720h refund window ----------------------
LID_720 = mk(10, {"refund_window_hours": 720})
ECHO_720 = {"rail": "escrow", "refund_window_hours": 720, "deposit_required": 0.0}
st, r = book(LID_720, extra={"accepted_payment_terms": ECHO_720})
check("T1 booking created (720h window)", st == 201 and r.get("escrow") == "HELD", f"got {st}: {r}")
BID_OLD = r.get("id", "")
CT_OLD = r.get("cancel_token", "")
ttl = tok_ttl(CT_OLD)
check("T1 cancel_token TTL >= 720h + 48h - slack",
      ttl >= (720 + 48) * 3600 - 120, f"ttl={ttl}s")
check("T1 old fixed 7d TTL would have been too short", ttl > 7 * 24 * 3600, f"ttl={ttl}s")

# --- T2: in-window cancel still refunds (positive path) -----------------
LID_72 = mk(10)  # default 72h events window
st, r = book(LID_72)
BID_72, CT_72 = r.get("id", ""), r.get("cancel_token", "")
st, r = post(HUB, f"/book/{BID_72}/cancel", {}, token=CT_72)
check("T2 in-window cancel -> REFUNDED",
      st == 200 and r.get("escrow") == "REFUNDED", f"got {st}: {r}")

# --- T3: aged booking -> 409 refund window closed -----------------------
_kill(_p)
time.sleep(0.5)
with open(STATE) as f:
    state = json.load(f)
aged = False
for b in state.get("bookings", []):
    if b.get("id") == BID_OLD and b.get("escrow") == "HELD":
        b["created"] = b.get("created", time.time()) - 721 * 3600  # past the 720h window
        aged = True
with open(STATE, "w") as f:
    json.dump(state, f)
check("T3 aged booking rewritten", aged, "booking not found in state")
HUB, _p, _log = spawn(STATE)
st, r = post(HUB, f"/book/{BID_OLD}/cancel", {}, token=CT_OLD)
check("T3 aged cancel -> 409 refund window closed",
      st == 409 and "refund window closed" in str(r.get("error", "")), f"got {st}: {r}")
check("T3 409 is honest about the chain",
      "auto-release" in str(r.get("note", "")) and "timeoutRefund" in str(r.get("hint", "")),
      f"note={r.get('note')!r} hint={r.get('hint')!r}")

# --- T4: WAIVED (free) bookings stay cancellable when aged ---------------
st, _ = post(HUB, "/listings", {"vertical": "events", "title": f"S7 free {time.time()}",
    "price": 0, "location": "Vienna", "capacity": 10, "date": "2026-12-01"}, token=LT)
st, lst = get(HUB, "/search?q=S7%20free")
LID_FREE = (lst.get("listings") or [{}])[0].get("id", "")
st, r = book(LID_FREE)
check("T4 free booking WAIVED", st == 201 and r.get("escrow") == "WAIVED", f"got {st}: {r}")
BID_FREE, CT_FREE = r.get("id", ""), r.get("cancel_token", "")
_kill(_p)
time.sleep(0.5)
with open(STATE) as f:
    state = json.load(f)
for b in state.get("bookings", []):
    if b.get("id") == BID_FREE and b.get("escrow") == "WAIVED":
        b["created"] = b.get("created", time.time()) - 800 * 3600
with open(STATE, "w") as f:
    json.dump(state, f)
HUB, _p, _log = spawn(STATE)
st, r = post(HUB, f"/book/{BID_FREE}/cancel", {}, token=CT_FREE)
check("T4 aged WAIVED cancel still 200 (no money moves)",
      st == 200 and r.get("escrow") == "REFUNDED", f"got {st}: {r}")

# --- verdict -------------------------------------------------------------
failed = [n for n, ok in RESULTS if not ok]
print(f"\ncancel-window: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("S7_CANCEL_WINDOW_ALL_PASSED")
