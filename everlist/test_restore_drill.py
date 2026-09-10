"""H2: backup restore drill (self-managed hub + tools/restore_backup.py).
The full loop: create real world -> force backups -> STOP hub -> restore an
EARLIER backup via the tool -> verify byte-exact + API-verifiable world.
Run: python test_restore_drill.py
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TMP = tempfile.mkdtemp(prefix="hub-h2-")
STATE = os.path.join(TMP, "state.json")
BDIR = os.path.join(TMP, "backups")
TOOL = os.path.join(HERE, "tools", "restore_backup.py")
PYEXE = sys.executable


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


def spawn_hub(port):
    logf = open(os.path.join(TMP, f"hub-{port}.log"), "a")
    env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_POW_SIGNUP_BITS": "8",
           "HUB_BACKUP_MIN_BYTES": "10", "HUB_BACKUP_KEEP": "10",
           "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    assert wait_ready(port)
    return p, logf


def stop(p, logf):
    p.terminate()
    try: p.wait(timeout=5)
    except Exception: p.kill()
    logf.close()
    time.sleep(0.3)


def run_tool(*args):
    r = subprocess.run([PYEXE, TOOL, "--state", STATE, *args],
                       capture_output=True, text=True, timeout=30)
    return r.returncode, r.stdout + r.stderr


def signup(port, i):
    seed = os.urandom(32).hex()
    pw = chatlib._pow_solve(f"http://127.0.0.1:{port}", "signup")
    st, res = chatlib._hub_post(f"http://127.0.0.1:{port}", "/accounts/signup",
                                {"agent": f"h2-{i}", "pubkey": chatlib._pubkey_of(seed), "pow": pw})
    assert st == 201, f"signup failed: {st} {res}"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---- phase 1: build a real world with 6 backup-eligible persists ----
PORT1 = free_port()
hub, logf = spawn_hub(PORT1)
for i in range(6):
    signup(PORT1, i)
time.sleep(0.4)
world_final = json.load(open(STATE))
n_final = len(world_final["listings"]) + 0  # hub seeds a few demo listings
baks = sorted(f for f in os.listdir(BDIR) if f.startswith("state-"))
# Discovery-first semantics: _backup_locked fires at the START of each persist
# and snapshots the PRE-persist state - the first persist has no prior file,
# so 6 signups produce exactly 5 backups (rotation cap never engaged here).
check("H2 six persists produced 5 pre-persist backups", len(baks) == 5, f"got {len(baks)}")
target = baks[1]  # restore an EARLIER world, not the last
target_hash = sha(os.path.join(BDIR, target))

# ---- phase 2: tool listing + dry-run + live-hub refusal ----
rc, out = run_tool("--list")
check("H2 --list shows backups with indices", rc == 0 and "[1]" in out and target in out)
rc, out = run_tool("--check", "--file", "1")
check("H2 --check is a no-op", rc == 0 and "dry-run" in out and sha(STATE) != target_hash)
# live-state refusal: the hub now HOLDS the state flock itself (H4 landed).
# Prove both halves: a non-blocking acquire fails while the hub runs, and the
# tool refuses to restore a live state. (Supersedes the old simulated
# lock-holder, which would now block forever against the real hub lock.)
lockf = open(STATE + ".lock", "a+")
import fcntl
try:
    fcntl.flock(lockf, fcntl.LOCK_EX | fcntl.LOCK_NB)
    fcntl.flock(lockf, fcntl.LOCK_UN)
    hub_holds = False
except OSError:
    hub_holds = True
lockf.close()
check("H2 hub holds the state flock while running (H4)", hub_holds)
rc, out = run_tool("--latest")
check("H2 refuses restore while hub is live", rc != 0 and "REFUSING" in out)

# ---- phase 3: stop hub, restore the earlier world ----
stop(hub, logf)
rc, out = run_tool("--file", "1")
check("H2 --file 1 restores cleanly", rc == 0 and "restored" in out, out[-200:])
check("H2 pre-restore undo file saved",
      any(f.startswith("state.pre-restore-") for f in os.listdir(TMP)))
check("H2 restored state is byte-identical to chosen backup", sha(STATE) == target_hash)

# ---- phase 4: restart hub on restored state -> API-verifiable world ----
PORT2 = free_port()
hub2, logf2 = spawn_hub(PORT2)
with urllib.request.urlopen(f"http://127.0.0.1:{PORT2}/listings", timeout=10) as r:
    lst = json.load(r)
restored_world = json.load(open(STATE))
check("H2 hub serves restored listings",
      lst["count"] == len(restored_world["listings"]),
      f"api={lst['count']} state={len(restored_world['listings'])}")
with urllib.request.urlopen(f"http://127.0.0.1:{PORT2}/ledger", timeout=10) as r:
    led = json.load(r)
check("H2 ledger consistent after restore", led.get("totals", {}).get("total_volume") is not None)

stop(hub2, logf2)

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== restore-drill: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)