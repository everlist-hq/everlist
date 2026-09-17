"""Jobs vertical regression suite (owner-approved Phase C, 2026-09-17).
Jobs ships as a C5 community schema (schemas/jobs.json) - this locks the
contract: schema loads fail-closed, apply+pay booking works with worker
identity protected, controlled vocab enforced, capacity tracked.
Run: python test_jobs.py
"""
import sys
import hashlib, json, os, socket, subprocess, sys, time, atexit, tempfile
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-jobs-"), "state.json")
env = {**os.environ, "HUB_STATE_FILE": STATE}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)

atexit.register(lambda: proc.terminate())


def req(method, path, payload=None, tok=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **({"X-Hub-Token": tok} if tok else {})},
        method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_ready(timeout=15):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            req("GET", "/.well-known/agent-hub.json"); return True
        except Exception:
            time.sleep(0.2)
    return False


def solve_pow(kind):
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def check(name, cond, detail=""):
    cond = bool(cond)
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" -- " + str(detail)[:200] if detail and not cond else ""))


assert wait_ready(), "hub did not start"
print("== jobs vertical (C5 community schema) ==")

c, v = req("GET", "/verticals")
check("jobs in /verticals", c == 200 and "jobs" in v.get("verticals", {}), (c, list(v.get("verticals", {}).keys())))
j = v.get("verticals", {}).get("jobs", {})
check("booking action apply+pay", j.get("booking", {}).get("action") == "apply+pay", j.get("booking"))
check("identity field is worker", j.get("booking", {}).get("identity") == "worker", j.get("booking"))
check("tracks worker capacity", j.get("tracks_capacity") is True)

# accounts
c, a = req("POST", "/accounts/signup", {"agent": "jb-org", "pow": solve_pow("signup")})
c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "jb-org"})
ltok = l["tokens"]["list"]
c, a2 = req("POST", "/accounts/signup", {"agent": "jb-buyer", "pow": solve_pow("signup")})
c, l2 = req("POST", "/accounts/login", {"account_code": a2["account_code"], "agent": "jb-buyer"})
btok = l2["tokens"]["book"]

# create job
c, li = req("POST", "/listings", {"title": "Barista for one morning shift", "date": "2026-10-02",
    "location": "Berlin", "price": 45, "capacity": 2, "vertical": "jobs",
    "category": "shift", "duration_minutes": 300}, tok=ltok)
check("job listing created", c == 201, (c, li))
lid = li["id"]

c, lrec = req("GET", f"/listings/{lid}", tok=ltok)
check("capacity tracked", c == 200 and lrec.get("capacity") == 2 and lrec.get("registered") == 0, (c, lrec))

# apply+pay
c, bk = req("POST", "/book", {"listing_id": lid, "quantity": 1,
    "human_verified": True, "worker": "Sam Worker"}, tok=btok)
check("apply+pay booking held", c == 201 and bk.get("escrow") in ("HELD", "WAIVED"), (c, bk))

c, bd = req("GET", f"/bookings/{bk['id']}", tok=btok)
check("worker identity anon-ref", c == 200 and bd.get("worker") not in (None, "Sam Worker"),
      (c, (bd or {}).get("worker")))

c, lrec2 = req("GET", f"/listings/{lid}", tok=ltok)
check("slot consumed", c == 200 and lrec2.get("registered") == 1, (c, lrec2.get("registered")))

# controlled vocab
c, bad = req("POST", "/listings", {"title": "Bad cat", "date": "2026-10-03", "location": "X",
    "price": 10, "capacity": 1, "vertical": "jobs", "category": "nonsense"}, tok=ltok)
check("bad category rejected", c >= 400, (c, bad))

print(f"\njobs suite: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
sys.exit(0)
