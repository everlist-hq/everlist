"""Storage E2E (Task 1.1 hardening): the unit tests prove the adapter; this
proves the SERVER. For each mode (file, sqlite): boot the hub, create a
listing + booking, RESTART the process, and prove persistence + idempotent
replay survive the restart. Then prove sqlite-mode fail-closed on a corrupt
DB (exit 78 — the same contract as legacy file mode).
Run: python test_storage_e2e.py
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
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close()
    return p


def req(base, method, path, body=None, headers=None):
    r = urllib.request.Request(base + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
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


def boot(td, mode, port):
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(td, "state.json"),
           "HUB_STORAGE_MODE": mode, "HUB_POW_SIGNUP_BITS": "8",
           "PYTHONUNBUFFERED": "1"}
    logf = open(os.path.join(td, "hub-%s.log" % mode), "a")
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                            stdout=logf, stderr=subprocess.STDOUT, env=env)
    end = time.time() + 15
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                raise AssertionError("hub died on boot (mode=%s) see %s" % (mode, logf.name))
            time.sleep(0.2)
    proc.terminate()
    raise AssertionError("hub not ready (mode=%s)" % mode)


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        proc.wait(timeout=5)


def scenario(mode):
    td = tempfile.mkdtemp(prefix="hub-store2e-%s-" % mode)
    base = "http://127.0.0.1:%d" % free_port()
    p1 = boot(td, mode, int(base.rsplit(":", 1)[1]))
    try:
        c, a = req(base, "POST", "/access", {"agent": "store2e", "acts": ["list", "book"]})
        assert c == 201, a
        tok, btok = a["tokens"]["list"], a["tokens"]["book"]
        c, l = req(base, "POST", "/listings",
                   {"vertical": "events", "title": "Storage E2E " + mode,
                    "date": "2026-12-01", "location": "Teststadt", "price": 0, "capacity": 5},
                   {"X-Hub-Token": tok})
        assert c == 201, l
        lid = l["id"]
        c, b = req(base, "POST", "/book", {"listing_id": lid, "attendee": "E2E Buyer",
                   "human_verified": True}, {"X-Hub-Token": btok, "Idempotency-Key": "e2e-" + mode})
        assert c == 201, b
        bid = b["id"]
    finally:
        stop(p1)

    state_json = os.path.join(td, "state.json")
    if mode == "sqlite":
        check("sqlite: hub.db exists", os.path.exists(os.path.join(td, "hub.db")))
        check("sqlite: no state.json side-file", not os.path.exists(state_json))
    else:
        check("file: state.json exists", os.path.exists(state_json))

    # fresh port: state lives in td, not in the port
    port2 = free_port()
    p2 = boot(td, mode, port2)
    base2 = "http://127.0.0.1:%d" % port2
    try:
        c, ls = req(base2, "GET", "/listings")
        check("%s: listing survives restart" % mode,
              c == 200 and any(x["id"] == lid for x in ls.get("listings", [])), str(c))
        c, bl = req(base2, "GET", "/bookings", headers={"X-Hub-Token": btok})
        check("%s: booking survives restart" % mode,
              c == 200 and any(x["id"] == bid for x in bl.get("bookings", [])), str(c))
        c, r = req(base2, "POST", "/book", {"listing_id": lid, "attendee": "E2E Buyer",
                   "human_verified": True}, {"X-Hub-Token": btok, "Idempotency-Key": "e2e-" + mode})
        check("%s: idempotent replay across restart" % mode,
              c == 201 and r.get("replayed") is True and r.get("id") == bid, str(c))
    finally:
        stop(p2)

    if mode == "sqlite":
        dbf = os.path.join(td, "hub.db")
        with open(dbf, "r+b") as f:
            f.seek(0)
            f.write(b"NOTADBNOTADBNOTAD")  # destroy the sqlite header
        env = {**os.environ, "HUB_STATE_FILE": os.path.join(td, "state.json"),
               "HUB_STORAGE_MODE": mode, "PYTHONUNBUFFERED": "1"}
        logf = open(os.path.join(td, "hub-corrupt.log"), "w")
        proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(free_port())],
                                stdout=logf, stderr=subprocess.STDOUT, env=env)
        try:
            rc = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            rc = None
        check("sqlite: corrupt db -> fail-closed exit 78", rc == 78, "rc=%s" % rc)


for m in ("file", "sqlite"):
    try:
        scenario(m)
    except AssertionError as ex:
        check("%s: scenario completed" % m, False, str(ex))

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== storage-e2e: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("STORAGE_E2E_ALL_PASSED")
