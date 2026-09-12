"""B5: SDK Tier-1 keypair account test (self-managed fresh hub).
Proves: signup_keypair (seed local, pubkey-only at hub) -> fresh client instance
login_seed -> add_listing -> ownership. Run: python test_sdk_accounts.py
"""
import sys
import json, os, socket, subprocess, sys, tempfile, time, atexit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "sdk"))
from agenthub import AgentHub, HubError  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-sdk-"), "state.json")
LOGF = os.path.join(os.path.dirname(STATE), "hub.log")
_ACTIVE = []
atexit.register(lambda: [_kill(p) for p in _ACTIVE])


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


log_fh = open(LOGF, "w")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log", "HUB_POW_SIGNUP_BITS": "8"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log_fh, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"

# 1) generate + signup
seed = AgentHub.generate_keypair()
check("B5 generate_keypair 64 hex", len(seed) == 64 and all(c in "0123456789abcdef" for c in seed))
clientA = AgentHub(BASE)
acct = clientA.signup_keypair("sdk-merchant", seed=seed)
check("B5 signup_keypair returns seed+pubkey+account", bool(acct.get("seed")) and bool(acct.get("pubkey")) and str(acct.get("account_id", "")).startswith("acct-"))
check("B5 returned seed matches requested", acct["seed"] == seed)

# 2) hub stores ONLY the pubkey — the seed must not appear anywhere in state
snap_raw = open(STATE).read()
check("B5 seed NEVER persisted at hub", seed not in snap_raw)
snap = json.loads(snap_raw)
acct_state = snap["accounts"][acct["account_id"]]
check("B5 hub stores pubkey", acct_state.get("pubkey") == acct["pubkey"])

# 3) login on a FRESH client instance (no shared state) using only the seed
clientB = AgentHub(BASE)
l = clientB.login_seed(seed, "sdk-merchant-b")
check("B5 fresh-instance login_seed works", bool(l.get("tokens", {}).get("list")))

# 4) create a listing with the fresh instance; verify ownership server-side
listing = clientB.add_listing(vertical="events", title="SDK Keypair Gig",
                              price=12.0, capacity=40, location="x",
                              date="2026-10-01")
check("B5 add_listing via keypair account", bool(listing.id))
snap2 = json.loads(open(STATE).read())
mine = [x for x in snap2["listings"] if x["id"] == listing.id]
check("B5 listing owned by account", mine and mine[0].get("owner") == acct["account_id"])

# 5) same principal: bookings() principal-scoping works for the fresh login
orders = clientB.orders()
check("B5 orders() reachable for account principal", isinstance(orders, list))

# 6) wrong seed is rejected with a real hub error (not a crash)
bad = AgentHub(BASE)
try:
    bad.login_seed("0" * 64, "sdk-attacker")
    check("B5 unknown-seed login rejected", False, "no error raised")
except HubError as e:
    check("B5 unknown-seed login rejected", e.status in (400, 401, 403, 404), f"status={e.status}")

_kill(proc); _ACTIVE.clear(); log_fh.close()
fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== sdk-accounts: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
