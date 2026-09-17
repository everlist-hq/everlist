"""H16: demo seed + reset - a pilot demo is reproducible in ONE command.
Proven: seed tool creates all demo listings (yoga class included) on a fresh
hub; the yoga class is findable by search; re-seeding an already-seeded hub is
honestly refused; seeding a SECOND fresh hub reproduces the same demo (ids
deterministic per vertical counter).
Run: python test_h16_demo_seed.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
N = len(json.load(open(os.path.join(HERE, "demo_listings.json")))["listings"])
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def start_hub(tmp):
    port = free_port()
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
    logf = open(os.path.join(tmp, "hub.log"), "a")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                            stdout=logf, stderr=subprocess.STDOUT, env=env)
    end = time.time() + 15
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc, logf, port
        except OSError:
            time.sleep(0.2)
    raise AssertionError("hub did not start")


def run_seed(port):
    return subprocess.run([sys.executable, os.path.join(HERE, "tools", "seed_demo.py"),
                           "--hub", f"http://127.0.0.1:{port}"],
                          capture_output=True, text=True, timeout=60)


EMPTY_SNAPSHOT = '{"listings": [], "bookings": [], "ledger": []}'


def fresh_empty_state(tmp):
    # H16: pre-create an empty snapshot so _load_state replaces the built-in
    # G2 seed data with [] -> a truly fresh hub for the demo flow.
    with open(os.path.join(tmp, "state.json"), "w") as f:
        f.write(EMPTY_SNAPSHOT)


def get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}" + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


tmp1 = tempfile.mkdtemp(prefix="hub-h16-a-")
fresh_empty_state(tmp1)  # H16: empty snapshot => built-in seed data replaced by []
proc1, logf1, port1 = start_hub(tmp1)
try:
    print("== H16: one command seeds the full demo ==")
    r = run_seed(port1)
    check("seed tool exit 0 on fresh hub", r.returncode == 0, r.stdout + r.stderr)
    check(f"all {N} demo listings created", r.stdout.count("seeded") == N, r.stdout)
    check("yoga class in demo output", "Free Surya Kriya Taster Class" in r.stdout)
    check("manage codes surfaced for owners", r.stdout.count("manage:") == N, r.stdout)
    st, res = get(port1, "/search?q=yoga")
    yoga = [l for l in res.get("listings", []) if "Surya Kriya" in l.get("title", "")]
    check("yoga findable via search after seeding", st == 200 and len(yoga) == 1, str(st))
    st, res = get(port1, "/listings")
    verts = {l["vertical"] for l in res.get("listings", [])}
    check("demo spans events + services + jobs verticals", verts == {"events", "services", "jobs"}, str(verts))
    ids1 = sorted(l["id"] for l in res.get("listings", []))

    print("== H16: honesty - refuse double-seed ==")
    r2 = run_seed(port1)
    check("re-seed refused (exit 1)", r2.returncode == 1, r2.stdout + r2.stderr)
    check("refusal points to demo-reset", "demo-reset" in r2.stdout + r2.stderr)
finally:
    proc1.terminate()
    try:
        proc1.wait(timeout=5)
    except Exception:
        proc1.kill()
    logf1.close()

print("== H16: determinism - fresh hub reproduces the same demo ==")
tmp2 = tempfile.mkdtemp(prefix="hub-h16-b-")
fresh_empty_state(tmp2)
proc2, logf2, port2 = start_hub(tmp2)
try:
    r = run_seed(port2)
    check("second fresh hub seeds identically (exit 0)", r.returncode == 0)
    st, res = get(port2, "/listings")
    ids2 = sorted(l["id"] for l in res.get("listings", []))
    check("same listing ids on fresh hub (deterministic demo)", ids1 == ids2,
          f"{ids1} vs {ids2}")
finally:
    proc2.terminate()
    try:
        proc2.wait(timeout=5)
    except Exception:
        proc2.kill()
    logf2.close()

fails = [n for n, ok in RESULTS if not ok]
print()
print(f"=== h16-demo-seed: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H16_DEMO_SEED_ALL_PASSED")
