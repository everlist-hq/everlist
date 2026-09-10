"""H12: ops request log hygiene.
Proves: structured request lines (method, path-without-query, status, latency,
source, rid) in a bounded rotating file; X-Request-Id echoed; query strings
NEVER logged (challenge nonces); rotation keeps the live file bounded; an
impossible log path degrades to unlogged (hub never breaks on logging).
Run: python test_h12_request_logging.py
"""
import json, os, socket, subprocess, sys, tempfile, time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def get(base, path):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, dict(r.headers), r.read().decode()

def start_hub(tmp, extra_env=None):
    PORT = free_port(); BASE = f"http://127.0.0.1:{PORT}"
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1", **(extra_env or {})}
    logf = open(os.path.join(tmp, "hub.log"), "a")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                            stdout=logf, stderr=subprocess.STDOUT, env=env)
    end = time.time() + 15
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return proc, logf, BASE
        except OSError:
            time.sleep(0.2)
    raise AssertionError("hub did not start")

print("== H12: structured request log + rid echo ==")
TMP = tempfile.mkdtemp(prefix="hub-h12-")
proc, logf, BASE = start_hub(TMP)
try:
    st, hdrs, _ = get(BASE, "/verticals")
    check("hub serves with logging enabled", st == 200)
    rid = hdrs.get("X-Request-Id", "")
    check("X-Request-Id echoed", rid.startswith("req-"), rid[:20])
    time.sleep(0.3)
    logpath = os.path.join(TMP, "requests.log")  # default: beside STATE_FILE
    line = open(logpath).read().strip().splitlines()[-1]
    check("structured line has method path status latency src rid",
          "GET" in line and "/verticals" in line and "-> 200" in line and "ms" in line
          and "src=" in line and rid in line, line[:120])
    print("== H12: query strings never logged ==")
    st, _, _ = get(BASE, "/challenge?nonce=TOPSECRET123")
    assert st == 200
    time.sleep(0.3)
    alllog = open(logpath).read()
    check("nonce from query NOT in log", "TOPSECRET123" not in alllog)
    check("challenge request still logged (path only)", " /challenge -> 200" in alllog)
    print("== H12: rotation keeps file bounded ==")
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

TMP2 = tempfile.mkdtemp(prefix="hub-h12-rot-")
proc, logf, BASE = start_hub(TMP2, {"HUB_LOG_MAX_BYTES": "800", "HUB_LOG_BACKUPS": "2"})
try:
    for i in range(30):
        get(BASE, "/verticals")
    time.sleep(0.4)
    live = os.path.join(TMP2, "requests.log")
    size = os.path.getsize(live) if os.path.exists(live) else 0
    check("live log bounded by max bytes (+1 line slack)", size <= 800 + 200, f"{size}B")
    check("rotation produced backup.1", os.path.exists(live + ".1"))
    n_lines = sum(1 for _ in open(live))
    check("live log still receiving lines after rotation", n_lines >= 1, str(n_lines))
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

print("== H12: impossible log path never breaks the hub ==")
TMP3 = tempfile.mkdtemp(prefix="hub-h12-bad-")
proc, logf, BASE = start_hub(TMP3, {"HUB_REQUEST_LOG": "/proc/definitely/forbidden/x.log"})
try:
    st, _, _ = get(BASE, "/verticals")
    check("hub serves despite unusable log path", st == 200)
    st, _, _ = get(BASE, "/listings")
    check("second request also fine (no per-request crash)", st == 200)
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h12-request-logging: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails); sys.exit(1)
print("H12_REQUEST_LOGGING_ALL_PASSED")
