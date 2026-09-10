"""H4: single-instance guard drill.
Two hubs on DIFFERENT ports sharing ONE state file would interleave writes and
corrupt it silently (the Makefile port guard cannot catch this). The second
hub must refuse with exit 79. Also proves: parallel hubs on DIFFERENT states
are legit, and a stale lock after a crash recovers automatically.
Run: python test_single_instance.py
"""
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TMP = tempfile.mkdtemp(prefix="hub-h4-")
STATE = os.path.join(TMP, "state.json")
PYEXE = sys.executable


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def spawn(port, state):
    logf = open(os.path.join(TMP, f"hub-{port}.log"), "a")
    env = {**os.environ, "HUB_STATE_FILE": state, "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    return p, logf


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


# ---- 1) first hub starts and holds the lock ----
P1 = free_port()
h1, l1 = spawn(P1, STATE)
check("H4 first hub starts and serves", wait_ready(P1))

# ---- 2) same state, different port -> exit 79, clear error, never listens ----
P2 = free_port()
h2, l2 = spawn(P2, STATE)
try:
    rc2 = h2.wait(timeout=10)
except Exception:
    rc2 = None
    h2.kill()
check("H4 second hub exits 79", rc2 == 79, f"rc={rc2}")
out2 = open(os.path.join(TMP, f"hub-{P2}.log")).read()
check("H4 refusal message explains the lock", "REFUSING to start" in out2 and "state lock" in out2)
time.sleep(0.3)
check("H4 refused hub never listened", not listening(P2))

# ---- 3) first hub unaffected by the refused start ----
with urllib.request.urlopen(f"http://127.0.0.1:{P1}/listings", timeout=10) as r:
    check("H4 first hub unaffected", r.status == 200)

# ---- 4) DIFFERENT state files -> parallel hubs are legit ----
STATE_B = os.path.join(TMP, "state-b.json")
P3 = free_port()
h3, l3 = spawn(P3, STATE_B)
check("H4 different-state hub starts fine", wait_ready(P3))

# ---- 5) crash recovery: SIGKILL h1 -> flock auto-released -> hub restarts ----
h1.kill()
h1.wait()
l1.close()
time.sleep(0.3)
P4 = free_port()
h4, l4 = spawn(P4, STATE)
started = wait_ready(P4)
check("H4 stale lock after crash auto-recovers", started)

for p, lf in ((h4, l4), (h3, l3)):
    p.terminate()
    try:
        p.wait(timeout=5)
    except Exception:
        p.kill()
    lf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== single-instance: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
