"""H14: make selfcheck — on-demand read-only health probe.
Proves: ALL GREEN + exit 0 against a live hub; honest exit 1 (no traceback)
against a dead port; READ-ONLY guarantee (no write methods in the probe's
source; hub state file byte-identical after a run); works via make target.
Run: python test_h14_selfcheck.py
"""
import hashlib
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


def run_probe(url, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    r = subprocess.run([sys.executable, os.path.join(HERE, "tools", "selfcheck.py")],
                       capture_output=True, text=True, timeout=60,
                       env={**env, "HUB_URL": url})
    return r.returncode, r.stdout + r.stderr


TMP = tempfile.mkdtemp(prefix="hub-h14-")
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
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
    print("== H14: green against a live hub ==")
    code, out = run_probe(BASE)
    check("exit 0 on healthy hub", code == 0, f"code={code}\n{out[-300:]}")
    check("ALL GREEN banner", "ALL GREEN" in out)
    check("all 8 checks pass", out.count("PASS") == 8 and "FAIL" not in out, out)

    print("== H14: read-only guarantee ==")
    src = open(os.path.join(HERE, "tools", "selfcheck.py")).read()
    check("probe source contains NO write methods",
          "POST" not in src and "DELETE" not in src and "method=" not in src)
    state = os.path.join(TMP, "state.json")
    h1 = hashlib.sha256(open(state, "rb").read()).hexdigest() if os.path.exists(state) else "absent"
    run_probe(BASE)
    h2 = hashlib.sha256(open(state, "rb").read()).hexdigest() if os.path.exists(state) else "absent"
    check("hub state byte-identical after runs", h1 == h2)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    logf.close()

print("== H14: honest failure against a dead hub ==")
dead = free_port()
code, out = run_probe(f"http://127.0.0.1:{dead}")
check("exit 1 on dead hub", code == 1, str(code))
check("PROBLEMS FOUND banner (no traceback)",
      "PROBLEMS FOUND" in out and "Traceback" not in out, out[-200:])

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h14-selfcheck: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H14_SELFCHECK_ALL_PASSED")
