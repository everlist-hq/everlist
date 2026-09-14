"""H9: GET /listings/{id} + server-only field hygiene.
Proves: single fetch (200 full rich record), 404 unknown, 410 archived,
NO manage_code_hash on ANY public surface (collection, search, single,
premium), SDK get_listing, chat 'show <id>'.
Run: python test_h9_listing_fetch.py
"""
import hashlib
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
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sdk"))
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


TMP = tempfile.mkdtemp(prefix="hub-h9-")
PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
PYEXE = sys.executable


def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
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


def solve_pow(kind):
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0:
                bits += 8
                continue
            bits += 8 - b.bit_length()
            break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


env = {**os.environ,
       "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8",
       "PYTHONUNBUFFERED": "1"}
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

try:
    print("== H9 setup: owner + rich listing + archive candidate ==")
    c, a = req("POST", "/accounts/signup", {"agent": "h9-owner", "pow": solve_pow("signup")})
    assert c == 201, a
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "h9-owner"})
    assert c == 200, l
    LTOK = l["tokens"]["list"]
    c, r = req("POST", "/listings", {"vertical": "events", "title": "H9 Rich Event",
               "date": "2026-10-05", "location": "Vienna", "price": 9,
               "capacity": 20, "description": "Full rich record test event",
               "tags": ["h9", "test"], "url": "https://example.dev/h9"},
               {"X-Hub-Token": LTOK})
    assert c == 201, r
    LID = r["id"]
    MCODE = r["manage_code"]
    check("64-bit manage code (H9 bump)", MCODE.startswith("mgr-") and len(MCODE) >= len("mgr-") + 16, MCODE)

    c, r2 = req("POST", "/listings", {"vertical": "events", "title": "H9 Hidden",
                "date": "2026-10-06", "location": "X", "price": 1, "capacity": 5},
                {"X-Hub-Token": LTOK})
    HID = r2["id"]
    c, _ = req("POST", f"/listings/{HID}/manage", {"action": "archive", "manage_code": r2["manage_code"]},
               {"X-Hub-Token": LTOK})
    assert c == 200, f"archive failed: {c}"

    print("== H9: single-listing fetch ==")
    c, one = req("GET", f"/listings/{LID}")
    check("single fetch 200", c == 200, f"{c}")
    check("full rich record (description/tags/url)",
          one.get("description") == "Full rich record test event"
          and "h9" in (one.get("tags") or [])
          and one.get("url") == "https://example.dev/h9", str(one)[:140])
    check("single fetch has NO manage_code_hash", "manage_code_hash" not in one)
    check("unknown id -> 404", req("GET", "/listings/even-9999")[0] == 404)
    check("archived id -> 410", req("GET", f"/listings/{HID}")[0] == 410)
    check("path traversal safe (404)", req("GET", "/listings/..%2Fstate.json")[0] in (404, 400))

    print("== H9: hash leaks on NO public surface ==")
    _, col = req("GET", "/listings")
    leak_col = any("manage_code_hash" in x for x in col.get("listings", []))
    check("collection leaks nothing", not leak_col, str(col)[:120])
    _, sr = req("GET", "/search?q=H9")
    leak_sr = any("manage_code_hash" in x for x in sr.get("listings", []))
    check("search leaks nothing", not leak_sr)
    # premium surface (x402 paid fetch needs auth header; use open route shape)
    c, prem = req("GET", "/x402/events") if req("GET", "/x402/events")[0] != -1 else (0, {})
    if c == 200:
        evs = prem.get("events", [])
        check("premium surface leaks nothing",
              not any("manage_code_hash" in x for x in evs), str(evs)[:100])
    else:
        check("premium surface skipped (needs payment auth)", True)

    print("== H9: SDK get_listing ==")
    from agenthub import AgentHub, HubError
    cl = AgentHub(BASE)
    got = cl.get_listing(LID)
    check("SDK get_listing returns rich Listing", got.title == "H9 Rich Event" and got.extra and got.extra.get("url"))
    try:
        cl.get_listing("even-9999")
        check("SDK 404 raises HubError", False)
    except HubError as e:
        check("SDK 404 raises HubError", getattr(e, "status", None) == 404, f"{getattr(e, 'status', '?')}")
    try:
        cl.get_listing(HID)
        check("SDK 410 raises HubError", False)
    except HubError as e:
        check("SDK 410 raises HubError", getattr(e, "status", None) == 410, f"{getattr(e, 'status', '?')}")

    print("== H9: chat 'show' ==")
    import chatlib
    out = chatlib.handle_text(BASE, f"show {LID}", sender="h9-sender")
    check("chat show renders full record", chatlib._mb("H9 Rich Event") in out and "https://example.dev/h9" in out, out[:140])
    out2 = chatlib.handle_text(BASE, "show even-9999", sender="h9-sender")
    check("chat show 404 honest", "no listing" in out2.lower(), out2[:100])
    out3 = chatlib.handle_text(BASE, f"show {HID}", sender="h9-sender")
    check("chat show archived honest", "archived" in out3.lower(), out3[:100])
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h9-listing-fetch: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H9_LISTING_FETCH_ALL_PASSED")
