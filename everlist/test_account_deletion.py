"""H7: account self-deletion (GDPR-style erasure) — done-when matrix.
Proves: account gone (tokens dead), owned listings archived (not destroyed),
ledger + booking records intact (the pseudonymous money trail survives),
other accounts untouched, typed-confirmation gate, per-source delete limiter
(counts failed attempts), and the chat two-step flow end-to-end.
Run: python test_account_deletion.py
"""
import hashlib
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
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


TMP = tempfile.mkdtemp(prefix="hub-h7-")
STATE = os.path.join(TMP, "state.json")
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
PYEXE = sys.executable


def req(method, path, body=None, headers=None, xff=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    if xff:
        r.add_header("X-Forwarded-For", xff)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def solve_pow(kind):
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0:
                bits += 8
                continue
            bits += 8 - b.bit_length()
            break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def signup(agent, xff=None):
    c, a = req("POST", "/accounts/signup", {"agent": agent, "pow": solve_pow("signup")}, xff=xff)
    assert c == 201, f"signup {c}: {a}"
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": agent}, xff=xff)
    assert c == 200, f"login {c}: {l}"
    return a, l["tokens"]


env = {**os.environ,
       "HUB_STATE_FILE": STATE,
       "HUB_POW_SIGNUP_BITS": "8",  # fast tests; production default is 18
       "HUB_LIMIT_DELETE": "5",
       "HUB_TRUST_PROXY": "1",
       "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15
ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            ready = True
            break
    except OSError:
        time.sleep(0.2)
assert ready, "test hub did not start"

try:
    print("== H7: hub-level deletion matrix ==")
    a1, t1 = signup("h7-victim", xff="10.7.0.1")
    aid = a1["account_id"]
    c, l = req("POST", "/listings", {"vertical": "events", "title": "VictimClass",
               "date": "2026-10-01", "location": "X", "price": 0, "capacity": 5},
               {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")
    assert c == 201, l
    lid = l["id"]

    a2, t2 = signup("h7-bystander", xff="10.7.0.2")
    c, l2 = req("POST", "/listings", {"vertical": "events", "title": "BystanderClass",
                "date": "2026-10-02", "location": "X", "price": 3, "capacity": 5},
                {"X-Hub-Token": t2["list"]}, xff="10.7.0.2")
    assert c == 201, l2

    # a booking against the victim's listing: must SURVIVE deletion
    # (interim stub-flag path: bookings require verified-human; H7 tests deletion, not verification)
    c, b = req("POST", "/book", {"listing_id": lid, "attendee": "H7 Booker", "human_verified": True},
               {"X-Hub-Token": t2["book"]}, xff="10.7.0.2")
    check("precondition: booking created against victim listing", c == 201, f"{c} {b}")
    bid = b.get("id", "")
    _, snap_before = req("GET", "/ledger")
    ledger_before = json.dumps(snap_before, sort_keys=True)

    check("anon DELETE refused", req("DELETE", "/accounts/me", {}, xff="10.7.9.9")[0] == 401)
    check("delete without typed confirm -> 400",
          req("DELETE", "/accounts/me", {}, {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")[0] == 400)
    c, res = req("DELETE", "/accounts/me", {"confirm": "acct-wrong"},
                 {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")
    check("wrong typed confirm -> 400", c == 400, f"{c} {res}")

    c, res = req("DELETE", "/accounts/me", {"confirm": aid},
                 {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")
    check("deletion with typed confirm -> 200", c == 200, f"{c} {res}")
    check("response reports archived listings", res.get("listings_archived") == 1, f"{res}")

    check("old token is dead after deletion",
          req("DELETE", "/accounts/me", {"confirm": aid},
              {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")[0] in (401, 403))
    check("logout-all with dead token -> 401/403",
          req("POST", "/accounts/logout-all", {},
              {"X-Hub-Token": t1["list"]}, xff="10.7.0.1")[0] in (401, 403))

    st = json.load(open(STATE))
    check("account erased from state", aid not in st.get("accounts", {}), str(list(st.get("accounts", {}))[:3]))
    vic = next((x for x in st["listings"] if x["id"] == lid), {})
    check("victim listing archived (not destroyed)", vic.get("archived") is True, f"{vic}")
    check("victim listing kept owner ref + escrow-relevant fields",
          vic.get("owner") == aid and "capacity" in vic, f"{vic}")
    check("booking record survived deletion",
          any(x.get("id") == bid for x in st.get("bookings", [])), str(st.get("bookings", []))[:120])
    check("ledger byte-identical across deletion",
          json.dumps(req("GET", "/ledger")[1], sort_keys=True) == ledger_before)

    st2, body = req("GET", "/listings")
    check("archived listing hidden from public search",
          all(x["id"] != lid for x in body.get("listings", [])))
    c, body = req("POST", "/book", {"listing_id": lid, "attendee": "Late Booker", "human_verified": True},
                  {"X-Hub-Token": t2["book"]}, xff="10.7.0.2")
    check("booking archived listing -> 409", c == 409, f"{c} {body}")

    c, l3 = req("POST", "/accounts/login", {"account_code": a2["account_code"], "agent": "h7-bystander"}, xff="10.7.0.2")
    check("bystander account untouched (login still works)", c == 200, f"{c}")
    _, pub = req("GET", "/listings")
    check("bystander listing still public",
          any(x["id"] == l2["id"] for x in pub.get("listings", [])))

    print("== H7: delete limiter (per source, failed attempts count) ==")
    sts = [req("DELETE", "/accounts/me", {}, xff="10.7.5.5")[0] for _ in range(6)]
    check("delete flood: first 5 allowed (401)", all(s == 401 for s in sts[:5]), f"{sts}")
    check("delete flood -> 429 on 6th", sts[5] == 429, f"{sts}")
    check("fresh source unaffected by flood",
          req("DELETE", "/accounts/me", {}, xff="10.7.5.6")[0] == 401)

    print("== H7: chat two-step flow (real chatlib, real hub) ==")
    sys.path.insert(0, HERE)
    import chatlib
    sender = "h7-chat-sender"
    r1 = chatlib.handle_text(BASE, "signup", sender=sender)
    check("chat signup creates session", "acct-" in r1, r1[:120])
    s = chatlib._SESSIONS[sender]
    chat_aid = s["account_id"]
    r2 = chatlib.handle_text(BASE, "delete-account", sender=sender)
    check("step 1 demands typed confirmation with id",
          f"delete-account confirm {chat_aid}" in r2 and "PERMANENT" in r2.upper(), r2[:160])
    r3 = chatlib.handle_text(BASE, "delete-account confirm acct-nope", sender=sender)
    check("mismatched confirmation refused", "mismatch" in r3.lower(), r3[:120])
    r4 = chatlib.handle_text(BASE, f"delete-account confirm {chat_aid}", sender=sender)
    check("step 2 erases account", "erased" in r4.lower() and chat_aid in r4, r4[:160])
    check("chat session ended after deletion", sender not in chatlib._SESSIONS)
    r5 = chatlib.handle_text(BASE, "whoami", sender=sender)
    check("whoami shows anonymous after deletion", "acct-" not in r5, r5[:120])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== account-deletion: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H7_ACCOUNT_DELETION_ALL_PASSED")
