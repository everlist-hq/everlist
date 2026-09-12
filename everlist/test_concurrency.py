"""B2 concurrency proof battery (backlog B2).
Self-managed fresh hub; threads hammer signup + listings; state.json is the judge.
Run: python test_concurrency.py
"""
import sys
import hashlib, json, os, socket, subprocess, sys, tempfile, time, atexit
import urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-concurrency-"), "state.json")
LOGF = os.path.join(os.path.dirname(STATE), "hub.log")
_ACTIVE = []
atexit.register(lambda: [_kill(p) for p in _ACTIVE])


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


def req(method, path, body=None, token=None, xff=None):
    h = {"Content-Type": "application/json"}
    if token: h["X-Hub-Token"] = token
    if xff: h["X-Forwarded-For"] = xff  # distinct virtual client sources (HUB_TRUST_PROXY=1)
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as ex:
        return -1, {"error": f"transport: {ex}"}


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


def solve_pow():
    _, ch = req("GET", "/auth/challenge?kind=signup")
    diff = ch["difficulty"]
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= diff:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


log_fh = open(LOGF, "w")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log",
       "HUB_TRUST_PROXY": "1", "HUB_POW_SIGNUP_BITS": "8"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log_fh, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"


N_SIGNUPS = 40
N_LISTINGS = 30


def one_signup(i):
    pw = solve_pow()
    return req("POST", "/accounts/signup", {"agent": f"cc-{i:02d}", "pow": pw}, xff=f"10.9.{i // 250}.{i % 250 + 1}")


print(f"== B2: {N_SIGNUPS} parallel signups from {N_SIGNUPS} distinct sources ==")
t0 = time.time()
with ThreadPoolExecutor(max_workers=N_SIGNUPS) as ex:
    signups = list(ex.map(one_signup, range(N_SIGNUPS)))
dt = time.time() - t0
statuses = [s for s, _ in signups]
bodies = [b for _, b in signups]
check("B2 zero 500s / transports on signup", not any(s >= 500 or s == -1 for s in statuses), f"statuses={sorted(set(statuses))} in {dt:.1f}s")
check("B2 every signup succeeded (2xx)", all(200 <= s < 300 for s in statuses))
codes = [b.get("account_code") for _, b in signups if isinstance(b, dict)]
check("B2 every signup returned a code", all(codes) and len(codes) == N_SIGNUPS)

snap = json.loads(open(STATE).read())
accounts = snap.get("accounts", {})
check("B2 state.json has exactly 40 accounts", len(accounts) == N_SIGNUPS, f"got {len(accounts)}")
aids = list(accounts.keys())
check("B2 zero duplicate account ids", len(aids) == len(set(aids)))
code_hashes = [v.get("code_hash") for v in accounts.values() if v.get("code_hash")]
check("B2 zero duplicate code hashes", len(code_hashes) == len(set(code_hashes)))
pubkeys = [v.get("pubkey") for v in accounts.values() if v.get("pubkey")]
check("B2 zero duplicate pubkeys", len(pubkeys) == len(set(pubkeys)))

print(f"== B2: {N_LISTINGS} parallel listings by one account ==")
code0 = codes[0]
c, l = req("POST", "/accounts/login", {"account_code": code0, "agent": "cc-00"}, xff="10.9.0.1")
assert c == 200 and l.get("tokens", {}).get("list"), (c, l)
tok = l["tokens"]["list"]


def one_listing(i):
    return req("POST", "/listings",
               {"vertical": "events", "title": f"B2 stress {i:02d}", "category": "meetup",
                "date": "2026-10-01", "price": 1, "location": "x", "capacity": 5},
               token=tok, xff="10.9.0.1")

t0 = time.time()
with ThreadPoolExecutor(max_workers=N_LISTINGS) as ex:
    creates = list(ex.map(one_listing, range(N_LISTINGS)))
dt = time.time() - t0
cstatuses = [s for s, _ in creates]
clisting_ids = [b.get("id") for _, b in creates if isinstance(b, dict)]
check("B2 zero 500s / transports on listings", not any(s >= 500 or s == -1 for s in cstatuses), f"statuses={sorted(set(cstatuses))} in {dt:.1f}s")
check("B2 every listing created (2xx)", all(200 <= s < 300 for s in cstatuses))
check("B2 zero duplicate listing ids", len([i for i in clisting_ids if i]) == N_LISTINGS and len(set(clisting_ids)) == N_LISTINGS)

snap2 = json.loads(open(STATE).read())
lst = snap2.get("listings", [])
mine = [x for x in lst if x.get("title", "").startswith("B2 stress")]
check("B2 state.json has all 30 listings", len(mine) == N_LISTINGS, f"got {len(mine)}")
check("B2 id counter exact (no lost/dup ids)", snap2.get("id_counters", {}).get("even", 0) >= N_LISTINGS)
nums = [int(x["id"].rsplit("-", 1)[1]) for x in mine]
check("B2 listing ids strictly unique ints", len(set(nums)) == len(nums))

# ================= cleanup + verdict =================
_kill(proc); _ACTIVE.clear(); log_fh.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== concurrency: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
