"""H11: SDK robustness — typed errors, central timeout, GET-only retry.
Flaky-stub proof: GET survives one transient 503 (single retry); POST is sent
EXACTLY ONCE even under 503 (never double-book); persistent failure raises
HubNetworkError (subclass of HubError); real-hub E2E unaffected.
Run: python test_h11_sdk_robustness.py
"""
import json, os, socket, subprocess, sys, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sdk"))
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

class Flaky(BaseHTTPRequestHandler):
    get_count = 0
    post_count = 0
    def log_message(self, *a): pass
    def do_GET(self):
        Flaky.get_count += 1
        if Flaky.get_count == 1:
            self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":"transient"}')
        else:
            body = json.dumps({"ok": True, "attempt": Flaky.get_count}).encode()
            self.send_response(200); self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_POST(self):
        Flaky.post_count += 1
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(503); self.end_headers(); self.wfile.write(b'{"error":"down"}')

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

STUB = free_port()
srv = ThreadingHTTPServer(("127.0.0.1", STUB), Flaky)
t = threading.Thread(target=srv.serve_forever, daemon=True); t.start()

from agenthub import AgentHub, HubError, HubNetworkError

try:
    print("== H11: flaky-stub retry semantics ==")
    cl = AgentHub(f"http://127.0.0.1:{STUB}")
    st, r = cl._request("GET", "/anything")
    check("GET retried once and succeeded after 503", st == 200 and r.get("attempt") == 2, f"{st} {r}")
    check("GET attempt count == 2 (single retry, no storm)", Flaky.get_count == 2, str(Flaky.get_count))
    try:
        cl._request("POST", "/book", body={"x": 1})
        check("POST 503 raises HubError", False)
    except HubError as e:
        check("POST 503 raises HubError (not swallowed)", getattr(e, "status", None) == 503)
    check("POST sent EXACTLY ONCE (no double-booking risk)", Flaky.post_count == 1, str(Flaky.post_count))

    print("== H11: typed network errors ==")
    dead = free_port()
    cl2 = AgentHub(f"http://127.0.0.1:{dead}", timeout=1.0)
    try:
        cl2._request("GET", "/x")
        check("dead port raises HubNetworkError", False)
    except HubNetworkError as e:
        check("dead port raises HubNetworkError", e.status is None and e.body.get("retryable") is True)
    except HubError:
        check("dead port raises HubNetworkError (got plain HubError)", False)
    try:
        cl2._request("POST", "/x", body={})
        check("dead-port POST also HubNetworkError (typed)", False)
    except HubNetworkError:
        check("dead-port POST also HubNetworkError (typed)", True)
    except HubError:
        check("dead-port POST also HubNetworkError (got plain HubError)", False)
    check("HubNetworkError caught as HubError (backward compat)", issubclass(HubNetworkError, HubError))
finally:
    srv.shutdown()

print("== H11: real-hub E2E unaffected ==")
TMP = tempfile.mkdtemp(prefix="hub-h11-")
PORT = free_port(); BASE = f"http://127.0.0.1:{PORT}"; PYEXE = sys.executable
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15; ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5): ready = True; break
    except OSError: time.sleep(0.2)
assert ready, "test hub did not start"
try:
    cl3 = AgentHub(BASE)
    # use the SDK's public signup path (PoW solved internally via B5 parity)
    res = cl3.signup_keypair("h11-agent")
    check("real hub: SDK keypair signup works", str(res.get("account_id", "")).startswith("acct-"), str(res)[:100])
    cl3.bootstrap("h11-agent", acts=("book", "list"))
    lst = cl3.add_listing("events", "H11 Event", 3.0, 5, date="2026-10-11", location="X")
    check("real hub: listing via robust client", lst.id.startswith("even-"), lst.id)
    try:
        cl3.get_listing("even-9999")
        check("real hub: HubError(404) contract intact", False)
    except HubError as e:
        check("real hub: HubError(404) contract intact", getattr(e, "status", None) == 404)
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h11-sdk-robustness: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails); sys.exit(1)
print("H11_SDK_ROBUSTNESS_ALL_PASSED")
