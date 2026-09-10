"""Hardening regression suite (B3c-email + archive + id counters).
Self-managed fresh hub with stdout CAPTURED (email codes are read from the log).
Run: python test_hardening.py
"""
import json, os, re, socket, subprocess, sys, time, atexit, tempfile
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-hardening-"), "state.json")
LOGF = os.path.join(os.path.dirname(STATE), "hub.log")
_ACTIVE = []
atexit.register(lambda: [_kill(p) for p in _ACTIVE])


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


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


log_fh = open(LOGF, "w")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log"}
proc = subprocess.Popen(["/opt/venv/bin/python", os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log_fh, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"


def last_code(pat):
    log_fh.flush()
    m = re.findall(pat, open(LOGF).read())
    return m[-1] if m else None


def signup_login(agent):
    c, a = req("POST", "/accounts/signup", {"agent": agent})
    assert c == 201, f"signup {c}: {a}"
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": agent})
    assert c == 200, f"login {c}"
    return a["account_code"], l.get("tokens", {})


# ================= section 1: email recovery chain =================
print("== email recovery (HUB_EMAIL_MODE=log) ==")
c, a = req("POST", "/accounts/signup", {"agent": "hard-email"})
code = a.get("account_code", "")
check("signup 201", c == 201)

check("bind rejects bad email", req("POST", "/accounts/email/bind",
      {"email": "not-an-email", "account_code": code})[0] == 400)
check("bind rejects without proof", req("POST", "/accounts/email/bind",
      {"email": "hard@example.dev"})[0] == 403)

c, b = req("POST", "/accounts/email/bind", {"email": "hard@example.dev", "account_code": code})
check("bind 200 logged", c == 200 and b.get("delivery") == "logged")
vc = last_code(r"verification code: ([A-F0-9]{6})")
check("verification code in log (dev mode)", bool(vc))

check("verify rejects wrong code", req("POST", "/accounts/email/verify",
      {"email": "hard@example.dev", "code": "000000"})[0] == 403)
c, v = req("POST", "/accounts/email/verify", {"email": "hard@example.dev", "code": vc})
check("verify 200", c == 200 and v.get("email_verified"))
check("verify code single-use", req("POST", "/accounts/email/verify",
      {"email": "hard@example.dev", "code": vc})[0] == 403)

c, r1 = req("POST", "/accounts/email/recover", {"email": "hard@example.dev"})
rc = last_code(r"recovery code: ([A-F0-9]{6})")
check("recover request logs code", c == 200 and bool(rc))
c, r2 = req("POST", "/accounts/email/recover", {"email": "unknown@example.dev"})
check("recover: no enumeration (same note)", r2.get("note") == r1.get("note") and r2.get("delivery") == "suppressed")

c, n = req("POST", "/accounts/email/recover/confirm", {"email": "hard@example.dev", "code": rc})
new_code = n.get("account_code", "")
check("recover-confirm mints new code", c == 200 and new_code.startswith("acct-"))
check("old code dead after recovery", req("POST", "/accounts/login",
      {"account_code": code, "agent": "hard-old"})[0] == 403)
check("new code works", req("POST", "/accounts/login",
      {"account_code": new_code, "agent": "hard-new"})[0] == 200)
check("recovery code single-use", req("POST", "/accounts/email/recover/confirm",
      {"email": "hard@example.dev", "code": rc})[0] == 403)

# ================= section 2: archive auth matrix =================
print("== archive auth matrix ==")
code_a, tok_a = signup_login("hard-owner-a")
c, li = req("POST", "/listings", {"vertical": "events", "title": "Hardening Gala",
            "category": "other", "date": "2026-12-20", "price": 5,
            "location": "V", "capacity": 3}, headers={"X-Hub-Token": tok_a["list"]})
lid = li.get("id")
check("create for archive test", c == 201 and lid)
_, tok_b = signup_login("hard-owner-b")

check("archive 200", req("POST", f"/listings/{lid}/manage", {"action": "archive"},
      headers={"X-Hub-Token": tok_a["list"]})[0] == 200)
check("anon archived view 401", req("GET", "/listings?archived=1")[0] == 401)
check("garbage token archived view 401", req("GET", "/listings?archived=1",
      headers={"X-Hub-Token": "garbage"})[0] == 401)
c, d = req("GET", "/listings?archived=1", headers={"X-Hub-Token": tok_b["list"]})
check("other owner sees none of A", c == 200 and not any(x["id"] == lid for x in d.get("listings", [])))
c, d = req("GET", "/listings?archived=1", headers={"X-Hub-Token": tok_a["list"]})
check("owner sees own archived", c == 200 and any(x["id"] == lid for x in d.get("listings", [])))
_, pub = req("GET", "/listings")
check("public view hides archived", not any(x["id"] == lid for x in pub.get("listings", [])))
_, s = req("GET", "/search?q=Hardening%20Gala")
check("search hides archived", s.get("count") == 0)

# archived refusal fires BEFORE field validation (after human gate)
btok = tok_a.get("book")
c, bk = req("POST", "/book", {"listing_id": lid, "quantity": 1, "human_verified": True,
            "attendee": "X"}, headers={"X-Hub-Token": btok})
check("archived booking 409 pre-validation", c == 409 and "archived" in bk.get("error", ""))
check("unarchive restores", req("POST", f"/listings/{lid}/manage", {"action": "unarchive"},
      headers={"X-Hub-Token": tok_a["list"]})[0] == 200)
_, s = req("GET", "/search?q=Hardening%20Gala")
check("searchable after unarchive", s.get("count") == 1)

# ================= section 3: id monotonicity =================
print("== id monotonicity (never reused, restart-stable) ==")
c, t1 = req("POST", "/listings", {"vertical": "events", "title": "ID A", "category": "other",
            "date": "2026-12-21", "price": 1, "location": "X", "capacity": 2},
            headers={"X-Hub-Token": tok_a["list"]})
id1 = t1.get("id")
req("POST", f"/listings/{id1}/manage", {"action": "delete"}, headers={"X-Hub-Token": tok_a["list"]})
c, t2 = req("POST", "/listings", {"vertical": "events", "title": "ID B", "category": "other",
            "date": "2026-12-22", "price": 1, "location": "X", "capacity": 2},
            headers={"X-Hub-Token": tok_a["list"]})
id2 = t2.get("id")
check("id not reused after delete", id1 != id2, f"{id1} vs {id2}")

# restart stability: counter survives process restart
_kill(proc); _ACTIVE.clear()
log2 = open(LOGF, "a")
proc = subprocess.Popen(["/opt/venv/bin/python", os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log2, stderr=subprocess.STDOUT, env={**env})
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not restart"
c, t3 = req("POST", "/listings", {"vertical": "events", "title": "ID C", "category": "other",
            "date": "2026-12-23", "price": 1, "location": "X", "capacity": 2},
            headers={"X-Hub-Token": tok_a["list"]})
id3 = t3.get("id")
check("id monotonic across restart", id3 not in (id1, id2), f"{id3}")

# ================= section 4: request-size limit =================
print("== request limits ==")
try:
    big = {"listing_id": "even-1", "junk": "x" * 100_000}
    r = urllib.request.Request(BASE + "/book", method="POST",
        data=json.dumps(big).encode(), headers={"Content-Type": "application/json"})
    urllib.request.urlopen(r, timeout=10)
    check("oversized body rejected", False, "accepted?!")
except urllib.error.HTTPError as e:
    check("oversized body rejected 413", e.code == 413, f"{e.code}")
except (BrokenPipeError, ConnectionResetError, urllib.error.URLError):
    check("oversized body rejected (connection cut)", True)

# ================= cleanup + verdict =================
req("POST", f"/listings/{id2}/manage", {"action": "delete"}, headers={"X-Hub-Token": tok_a["list"]})
req("POST", f"/listings/{id3}/manage", {"action": "delete"}, headers={"X-Hub-Token": tok_a["list"]})
req("POST", f"/listings/{lid}/manage", {"action": "delete"}, headers={"X-Hub-Token": tok_a["list"]})
_kill(proc); _ACTIVE.clear(); log_fh.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== hardening: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
