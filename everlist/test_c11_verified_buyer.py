"""C11: verified-buyer gating (SPEC section 22, owner-approved).

`require_verified_buyer: true` on a listing restricts booking to Tier-2-verified
accounts. The client-asserted human_verified stub NEVER satisfies the gate —
only server-side verification counts (admin-vouch pilot / midnight-zk).

Covered here:
- creation validation (boolean wall) + flag visible in the public payload
- gate enforcement: stub principals (anonymous + unverified accounts) walled
- admin-vouch path books; revoked midnight credential cannot verify or book
- midnight-zk path books (fixture verifier, simulated mode honestly labeled)
- ungated listings unchanged (stub still works — regression)
- owner can flip the gate via manage edit (boolean wall; non-owner cannot)
- private deal + gate: claim code AND verification are independent walls
- chat: verified_only strict parsing (yes/no; junk refused), flag reaches hub

Run: python test_c11_verified_buyer.py
"""
import atexit
import hashlib
import json
import os
import re
import shutil
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


def post(base, path, body, token=None, headers=None):
    h = {"Content-Type": "application/json"}
    if token: h["X-Hub-Token"] = token
    if headers: h.update(headers)
    rq = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                method="POST", headers=h)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e:
        return -1, {"EXC": f"{type(e).__name__}: {e}"}


def get(base, path, token=None):
    headers = {}
    if token: headers["X-Hub-Token"] = token
    rq = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e:
        return -1, {"EXC": f"{type(e).__name__}: {e}"}


TMP = tempfile.mkdtemp(prefix="hub-c11-")
STATE = os.path.join(TMP, "state.json")
FIX = os.path.join(TMP, "timeline.json")
shutil.copy(os.path.join(HERE, "fixtures", "credential-timeline.json"), FIX)

PORT = free_port()
HUB = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "HUB_STATE_FILE": STATE,
       "HUB_POW_SIGNUP_BITS": "8", "HUB_CRED_FIXTURE": FIX,
       "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"
ADMIN = {"X-Admin-Key": "dev-admin-key-change-me"}


def solve_pow(kind):
    _, ch = get(HUB, f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]: return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def acct(name):
    """signup+login; returns (account_id, account_code, tokens dict)."""
    c, r = post(HUB, "/accounts/signup", {"agent": name, "pow": solve_pow("signup")})
    assert c == 201, r
    c, l = post(HUB, "/accounts/login", {"account_code": r["account_code"], "agent": name + "-sess"})
    assert c == 200, l
    return r["account_id"], r["account_code"], l["tokens"]


def tokens(agent, acts):
    _, r = post(HUB, "/access", {"agent": agent, "acts": acts})
    return r.get("tokens", {})


def mk(gate=False, price=5, visibility="public", agent="c11-merchant"):
    body = {"vertical": "events", "title": f"C11 {time.time()} {price}", "price": price,
            "location": "Vienna", "capacity": 10, "date": "2026-12-01"}
    if gate: body["require_verified_buyer"] = True
    if visibility != "public": body["visibility"] = visibility
    st, r = post(HUB, "/listings", body, token=tokens(agent, ["list"])["list"])
    assert st == 201, f"seed failed: {st} {r}"
    return r["id"], r


def book(lid, extra=None, token=None):
    body = {"listing_id": lid, "attendee": "C11 Buyer", "human_verified": True}
    if extra: body.update(extra)
    return post(HUB, "/book", body, token=token)


print("== C11: creation validation + public face ==")
GATED, r_gated = mk(gate=True)
check("gated listing created", bool(GATED), str(r_gated)[:80])
c, l = post(HUB, "/listings", {"vertical": "events", "title": "null gate", "price": 5,
                               "location": "V", "capacity": 3, "date": "2026-12-01",
                               "require_verified_buyer": None},
            token=tokens("c11-merchant3", ["list"])["list"])
check("null gate treated as unset (listing created, no gate)",
      c == 201, f"{c} {l}")
c, l = get(HUB, f"/listings/{GATED}")
check("flag visible in public payload (buyers see the gate pre-booking)",
      c == 200 and l.get("require_verified_buyer") is True)
c, l = post(HUB, "/listings", {"vertical": "events", "title": "bad gate", "price": 5,
                               "location": "V", "capacity": 3, "date": "2026-12-01",
                               "require_verified_buyer": "yes"},
            token=tokens("c11-merchant2", ["list"])["list"])
check("string gate rejected (must be a real boolean)", c == 400, f"{c} {l}")
PLAIN, _r = mk(gate=False)
c, l = get(HUB, f"/listings/{PLAIN}")
check("ungated listing carries no gate (regression)", c == 200 and not l.get("require_verified_buyer"))

print("== C11: the stub NEVER satisfies the gate ==")
STUB = tokens("c11-stub", ["book"])["book"]
c, e = book(GATED, token=STUB)
check("anonymous stub principal walled (403)", c == 403, f"{c} {e}")
check("error names the Tier-2 requirement", "Tier-2-verified buyer" in str(e.get("error", "")), str(e))
check("error tells the fix (verify-midnight)", "verify-midnight" in str(e.get("note", "")), str(e))
_aid_u, _code_u, tok_u = acct("c11-unver")
c, e = book(GATED, token=tok_u["book"])
check("unverified account walled (stub flag changes nothing)", c == 403, f"{c} {e}")

print("== C11: verified paths ==")
_aid_v, _code_v, tok_v = acct("c11-vouched")
c, w = post(HUB, "/accounts/vouch", {"account_id": _aid_v}, headers=ADMIN)
check("operator vouch 200 (admin-vouch)", c == 200 and w.get("verified_by") == "admin-vouch", f"{c} {w}")
c, b = book(GATED, token=tok_v["book"])
check("admin-vouched account books the gated listing", c == 201, f"{c} {b}")

_aid_r, _code_r, tok_r = acct("c11-revoked")
c, e = post(HUB, "/accounts/verify-midnight", {"credential_id": "2", "account_code": _code_r})
check("revoked credential -> 403 (fail-closed)", c == 403, f"{c} {e}")
c, e = book(GATED, token=tok_r["book"])
check("revoked-credential account cannot book", c == 403, f"{c} {e}")

_aid_m, _code_m, tok_m = acct("c11-holder")
c, w = post(HUB, "/accounts/verify-midnight", {"credential_id": "1", "account_code": _code_m})
check("active credential -> Tier-2 verified (midnight-zk)", c == 200 and w.get("verified_by") == "midnight-zk", f"{c} {w}")
check("verification source honestly labeled (simulated)", w.get("mode") == "simulated", str(w.get("mode")))
c, b = book(GATED, token=tok_m["book"])
check("midnight-zk account books the gated listing", c == 201, f"{c} {b}")
_state = json.load(open(STATE))
_mb = next((bk for bk in _state.get("bookings", [])
            if bk.get("listing_id") == GATED and bk.get("booked_by") == _aid_m), None)
check("booking carries server-side verified_by (midnight-zk)",
      bool(_mb) and _mb.get("verified_by") == "midnight-zk",
      str((_mb or {}).get("verified_by")))

c, b = book(PLAIN, token=STUB)
check("ungated listing still accepts stub booking (behavior unchanged)", c == 201, f"{c} {b}")

print("== C11: owner edit flips (pre-booking only) ==")
_aid_o, _code_o, tok_o = acct("c11-owner")
st, r = post(HUB, "/listings", {"vertical": "events", "title": "C11 owner-edit", "price": 5,
                                "location": "V", "capacity": 5, "date": "2026-12-02",
                                "require_verified_buyer": True}, token=tok_o["list"])
assert st == 201, r
OWN = r["id"]
STUB2 = tokens("c11-stub2", ["book"])["book"]
c, e = book(OWN, token=STUB2)
check("owner-created gated listing walls stubs", c == 403, f"{c} {e}")
c, r = post(HUB, f"/listings/{OWN}/manage", {"action": "edit", "require_verified_buyer": False}, token=tok_o["list"])
check("owner flips gate off (single-field edit)", c == 200 and r.get("fields") == ["require_verified_buyer"], f"{c} {r}")
c, b = book(OWN, token=STUB2)
check("after flip-off the stub books fine", c == 201, f"{c} {b}")
c, r = post(HUB, f"/listings/{OWN}/manage", {"action": "edit", "require_verified_buyer": "banana"}, token=tok_o["list"])
check("edit rejects non-boolean gate", c == 400, f"{c} {r}")
c, r = post(HUB, f"/listings/{OWN}/manage", {"action": "edit", "require_verified_buyer": True}, token=tok_u["list"])
check("non-owner cannot flip the gate", c == 403, f"{c} {r}")
c, r = post(HUB, f"/listings/{OWN}/manage", {"action": "edit", "require_verified_buyer": True}, token=tok_o["list"])
check("owner flips gate back on", c == 200, f"{c} {r}")
c, e = book(OWN, token=STUB2)
check("gate re-armed: stub walled again", c == 403, f"{c} {e}")

print("== C11: private deal + gate = two independent walls ==")
GPRIV, r_p = mk(gate=True, visibility="private")
CLAIM = r_p.get("claim_code", "")
check("private deal mints claim code", bool(CLAIM))
c, e = book(GPRIV, extra={"claim": CLAIM}, token=STUB)
check("valid claim but stub -> 403 (claim != verification)", c == 403, f"{c} {e}")
c, b = book(GPRIV, extra={"claim": CLAIM}, token=tok_v["book"])
check("claim + verified account -> booked", c == 201, f"{c} {b}")

print("== C11: chat wiring ==")
import chatlib  # noqa: E402
r = chatlib.handle_text(HUB, "list\ntitle: Gate Chat\nprice: 5\nverified_only: banana", sender="c11-chat")
check("chat refuses junk verified_only with guidance", "verified_only must be yes or no" in r, r[:120])
r = chatlib.handle_text(HUB, "list\ntitle: Gate Chat Yes\nprice: 5\nverified_only: yes", sender="c11-chat")
m = re.search(r"id: ([a-z]+-\d+)", r)
check("chat rich listing with verified_only: yes created", bool(m), r[:120])
if m:
    c, l = get(HUB, f"/listings/{m.group(1)}")
    check("chat flag reached the hub", c == 200 and l.get("require_verified_buyer") is True,
          str(l.get("require_verified_buyer")))
r = chatlib.handle_text(HUB, "list\ntitle: Gate Chat No\nprice: 5\nverified_only: NO", sender="c11-chat2")
m2 = re.search(r"id: ([a-z]+-\d+)", r)
check("chat verified_only: NO accepted (case-insensitive)", bool(m2), r[:120])
if m2:
    c, l = get(HUB, f"/listings/{m2.group(1)}")
    check("chat NO leaves the listing ungated", c == 200 and not l.get("require_verified_buyer"))
check("help mentions the verified_only key", "verified_only" in chatlib._HELP)


print()
passed = sum(1 for _, ok in RESULTS if ok)
print(f"{passed}/{len(RESULTS)} checks passed")
sys.exit(0 if passed == len(RESULTS) else 1)
