"""H13: OpenAPI contract — the spec cannot lie about a route.
Loads /openapi.json from a live hub; for EVERY documented operation, proves
the route is actually handled: its response differs from the method's generic
404 catch-all (measured via junk control routes). Also: manifest links the
contract (api_contract), SPEC/README point to it, 402 premium wall intact.
Run: python test_h13_openapi.py
"""
import json, os, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def req(base, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(base + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}

def placeholder(path):
    sub = "even-nonexistent" if path.startswith("/listings/") else "bk-nonexistent"
    return path.replace("{id}", sub)

TMP = tempfile.mkdtemp(prefix="hub-h13-")
PORT = free_port(); BASE = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15; ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5): ready = True; break
    except OSError: time.sleep(0.2)
assert ready, "test hub did not start"

try:
    print("== H13: contract served + linked ==")
    st, spec = req(BASE, "GET", "/openapi.json")
    check("GET /openapi.json -> 200", st == 200, str(st))
    check("valid OpenAPI 3.1 skeleton",
          str(spec.get("openapi", "")).startswith("3.1") and "paths" in spec and "info" in spec)
    n_ops = sum(len(m) for m in spec.get("paths", {}).values())
    check("substantial coverage (>=25 documented operations)", n_ops >= 25, str(n_ops))
    st, man = req(BASE, "GET", "/.well-known/agent-hub.json")
    check("manifest links api_contract -> /openapi.json",
          man.get("api_contract") == "/openapi.json", str(man.get("api_contract")))
    check("SPEC.md documents /openapi.json", "/openapi.json" in open(os.path.join(HERE, "SPEC.md")).read())
    check("README points to /openapi.json", "openapi.json" in open(os.path.join(HERE, "README.md")).read())

    print("== H13: control catch-alls (discriminator has teeth) ==")
    sig = {}
    for m, junk in (("GET", "/h13-junk-get"), ("POST", "/h13-junk-post"), ("DELETE", "/h13-junk-del")):
        s, b = req(BASE, m, junk, body={} if m != "GET" else None)
        sig[m] = (s, json.dumps(b, sort_keys=True))
        if m in ("GET", "POST"):
            check(f"{m} junk route IS the 404 catch-all", s == 404, f"{s} {b}")

    print("== H13: the spec cannot lie — every documented op is handled ==")
    liars = []
    for path, methods in spec["paths"].items():
        for method in methods:
            s, b = req(BASE, method, placeholder(path), body={} if method in ("POST", "DELETE") else None)
            if (s, json.dumps(b, sort_keys=True)) == sig[method.upper()]:
                liars.append(f"{method} {path}")
    check("no documented route falls through to the catch-all", not liars, str(liars))
    s, b = req(BASE, "GET", "/listings/even-nonexistent")
    check("unknown listing -> route-specific 404 (not catch-all)",
          s == 404 and b.get("error") != "not found", f"{s} {b}")
    s, b = req(BASE, "GET", "/premium/events")
    check("premium -> 402 payment wall as documented", s == 402, str(s))
    s, b = req(BASE, "POST", "/access", body={})
    check("POST /access -> handled validation error", s == 400, f"{s} {b}")
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h13-openapi: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails); sys.exit(1)
print("H13_OPENAPI_ALL_PASSED")
