"""H5: write-path limiter flood test (per-source backstops on mutating routes).
Each new limiter kind (access/create/book/manage/admin) is flooded from its OWN
spoofed source; manage/admin floods use WRONG secrets — proving FAILED attempts
count (the brute-force property). Plus per-source fairness: a flooded source
stays limited while fresh sources keep working.
Run: python test_write_limits.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

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


TMP = tempfile.mkdtemp(prefix="hub-h5-")
PORT = free_port()
PYEXE = sys.executable


def req(method, path, body=None, headers=None, xff=None):
    data = None
    if body is not None:
        data = json.dumps(body).encode()
    r = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method)
    r.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    if xff:
        r.add_header("X-Forwarded-For", xff)  # distinct virtual sources (HUB_TRUST_PROXY=1)
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


# tiny limits so floods are fast; setup source stays far under all of them
env = {**os.environ,
       "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_LIMIT_ACCESS": "5", "HUB_LIMIT_CREATE": "5", "HUB_LIMIT_BOOK": "5",
       "HUB_LIMIT_MANAGE": "5", "HUB_LIMIT_ADMIN": "3",
       "HUB_TRUST_PROXY": "1", "PYTHONUNBUFFERED": "1"}
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

SETUP = "10.5.9.254"  # source used only for setup, never flooded
st, body = req("POST", "/access", {"agent": "setup", "acts": ["book", "list"]}, xff=SETUP)
assert st == 201, f"setup access failed: {st} {body}"
toks = body["tokens"]
TOK, LTOK = toks["book"], toks["list"]

# two listings from setup: big one for book flood, small one for manage flood
st, body = req("POST", "/listings", {"vertical": "events", "title": "Book flood target",
               "date": "2026-10-01", "location": "X", "price": 1, "capacity": 100},
               {"X-Hub-Token": LTOK}, xff=SETUP)
assert st == 201, body
LID_BIG = body["id"]
st, body = req("POST", "/listings", {"vertical": "events", "title": "Manage flood target",
               "date": "2026-10-02", "location": "X", "price": 1, "capacity": 5},
               {"X-Hub-Token": LTOK}, xff=SETUP)
assert st == 201, body
LID_SMALL = body["id"]

# ---- 1) access: 6th mint from one source -> 429 (limit 5) ----
sts = [req("POST", "/access", {"agent": "flood-a"}, xff="10.5.1.9")[0] for _ in range(6)]
check("H5 access flood: first 5 allowed", all(s == 201 for s in sts[:5]), f"{sts}")
check("H5 access flood -> 429 on 6th", sts[5] == 429, f"{sts}")

# ---- 2) create: 6th listing from one source -> 429 ----
sts = [req("POST", "/listings", {"vertical": "events", "title": f"flood {i}",
          "date": "2026-10-03", "location": "X", "price": 1, "capacity": 5},
          {"X-Hub-Token": LTOK}, xff="10.5.2.9")[0] for i in range(6)]
check("H5 create flood -> 429 on 6th", sts[5] == 429, f"{sts}")

# ---- 3) book: 6th booking from one source -> 429 ----
sts = [req("POST", "/book", {"listing_id": LID_BIG, "attendee": f"f{i}", "quantity": 1,
          "human_verified": True}, {"X-Hub-Token": TOK}, xff="10.5.3.9")[0] for i in range(6)]
check("H5 book flood -> 429 on 6th", sts[5] == 429, f"{sts}")

# ---- 4) manage: WRONG-code guesses are 403 and COUNT -> 6th -> 429 ----
sts = [req("POST", f"/listings/{LID_SMALL}/manage", {"action": "edit",
          "manage_code": "mgr-wrongguess", "title": "hacked"},
          {"X-Hub-Token": LTOK}, xff="10.5.4.9")[0] for _ in range(6)]
check("H5 manage brute-force: wrong codes are 403", all(s == 403 for s in sts[:5]), f"{sts}")
check("H5 manage brute-force -> 429 on 6th attempt", sts[5] == 429, f"{sts}")

# ---- 5) admin: WRONG-key guesses are 403 and COUNT -> 4th -> 429 (limit 3) ----
sts = [req("POST", "/admin/tokens", {"act": "confirm", "booking_id": "bk-x"},
          {"X-Hub-Token": "wrong-admin-key"}, xff="10.5.5.9")[0] for _ in range(4)]
check("H5 admin brute-force: wrong keys are 403", all(s == 403 for s in sts[:3]), f"{sts}")
check("H5 admin brute-force -> 429 on 4th attempt", sts[3] == 429, f"{sts}")

# ---- 6) fairness: flooded kinds do NOT affect fresh sources ----
st, _ = req("POST", "/access", {"agent": "fresh"}, xff="10.5.6.9")
check("H5 fairness: fresh source still mints tokens", st == 201)
st, _ = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": "bk-x"},
            {"X-Hub-Token": "wrong-admin-key"}, xff="10.5.7.9")
check("H5 fairness: fresh source gets 403 not 429 on admin", st == 403)

proc.terminate()
try:
    proc.wait(timeout=5)
except Exception:
    proc.kill()
logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== write-limits: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
