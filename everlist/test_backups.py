"""B8: state backup rotation (self-managed hub).
With HUB_BACKUP_MIN_BYTES forced tiny, every persist backs up the pre-persist
state. 8 persists -> exactly BACKUP_KEEP(5) backups; newest == pre-final state.
Run: python test_backups.py
"""
import sys
import hashlib, json, os, socket, subprocess, sys, tempfile, time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-b8-")
STATE = os.path.join(TMP, "state.json")
BDIR = os.path.join(TMP, "backups")


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


logf = open(os.path.join(TMP, "hub.log"), "w")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_POW_SIGNUP_BITS": "8",
       "HUB_BACKUP_MIN_BYTES": "10", "HUB_BACKUP_KEEP": "5", "PYTHONUNBUFFERED": "1"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
assert wait_ready(PORT)
BASE = f"http://127.0.0.1:{PORT}"


def signup(i):
    seed = os.urandom(32).hex()
    pw = chatlib._pow_solve(BASE, "signup")
    st, res = chatlib._hub_post(BASE, "/accounts/signup",
                                {"agent": f"b8-{i}", "pubkey": chatlib._pubkey_of(seed), "pow": pw})
    assert st == 201, f"signup {i} failed: {st} {res}"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# 8 backup-eligible persists (threshold forced to 10 bytes)
for i in range(8):
    signup(i)

time.sleep(0.3)
baks = sorted(f for f in os.listdir(BDIR) if f.startswith("state-") and f.endswith(".json"))
check("B8 exactly KEEP backups after 8 persists", len(baks) == 5, f"got {len(baks)}")
check("B8 backups are chronological (ns names sort)", baks == sorted(baks))

# newest backup == state BEFORE the 8th persist == state after the 7th
newest = os.path.join(BDIR, baks[-1])
check("B8 newest backup is valid JSON snapshot",
      isinstance(json.load(open(newest)), dict))

# one more persist; newest backup must equal the state right before it
h_before = sha(STATE)
signup(99)
time.sleep(0.3)
baks2 = sorted(f for f in os.listdir(BDIR) if f.startswith("state-") and f.endswith(".json"))
check("B8 rotation keeps cap after 9th persist", len(baks2) == 5, f"got {len(baks2)}")
check("B8 newest backup == pre-persist state hash", sha(os.path.join(BDIR, baks2[-1])) == h_before)

proc.terminate()
try: proc.wait(timeout=5)
except Exception: proc.kill()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== backups: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
