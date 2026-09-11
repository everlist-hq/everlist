#!/usr/bin/env python3
"""D3: conformance checker tests.

  D1  real hub with booking traffic -> CONFORMANT (exit 0)
  D2  empty-ledger hub -> INSUFFICIENT-EVIDENCE (exit 0)
  D3  tampered ledger (arithmetic broken) -> NON-CONFORMANT (exit 1)
  D4  fee mismatch (charges != declared) -> NON-CONFORMANT (exit 1)
  D5  totals lie vs raw entries -> NON-CONFORMANT (exit 1)
  D6  unknown escrow state -> NON-CONFORMANT (exit 1)
  D7  accounts advertised + valid challenge -> CONFORMANT (C6 ok)
  D8  accounts advertised + challenge 404 -> NON-CONFORMANT
  D9  accounts advertised + malformed challenge -> NON-CONFORMANT

Negative fixtures are served by a local stub hub (manifest + /ledger).
"""
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# layout-portable: works in experiments/ (agent-hub-v2) AND the published repo (everlist)
_CANDIDATES = [os.path.join(HERE, "..", "everlist"), os.path.join(HERE, "..", "agent-hub-v2")]
HUB_DIR = next((c for c in _CANDIDATES if os.path.exists(os.path.join(c, "app.py"))), _CANDIDATES[0])
PY = sys.executable  # same interpreter as this test (venv-portable)
CHECK = os.path.join(HERE, "check_hub.py")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + f" | {name}" + (f" | {detail}" if detail and not cond else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def run_check(url):
    r = subprocess.run([PY, CHECK, url], capture_output=True, text=True, timeout=60)
    verdict = ""
    for line in r.stdout.splitlines():
        if line.startswith("VERDICT:"):
            verdict = line.split(": ", 1)[1].strip()
    return r.returncode, verdict, r.stdout


def req(url, method="GET", body=None, headers=None):
    r = urllib.request.Request(url, method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def start_real_hub(with_traffic):
    port = free_port()
    state = os.path.join("/tmp", f"hub_d3_{uuid.uuid4().hex[:6]}.json")
    env = {**os.environ, "HUB_STATE_FILE": state}
    p = subprocess.Popen([PY, os.path.join(HUB_DIR, "app.py"), str(port)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    assert wait_ready(port)
    base = f"http://127.0.0.1:{port}"
    if with_traffic:
        st, acc = req(base + "/access", "POST", {"agent": "d3", "acts": ["book"]})
        assert st == 201
        req(base + "/book", "POST", {"listing_id": "evt-1", "attendee": "D",
            "quantity": 1, "human_verified": True}, {"X-Hub-Token": acc["tokens"]["book"]})
    return p, base, state


# ---- stub hub serving manipulated fixtures ----

MANIFEST = {"hub": "stub", "protocol": "agent-hub/0.2",
            "fairness": {"fee_policy": {"actual_fee_pct": 1.0}, "ledger": "/ledger",
                         "mirror": {"sync": "/admin/sync-escrow", "rail": "midnight-shielded-escrow",
                                    "policy": "chain is source of truth; sync never overwrites downward (C7)"}},
            "payments": {}, "capabilities": {}}


def make_stub(ledger, manifest=None, extra_get=None):
    """extra_get: {path: payload} served before the standard 404 (C6 fixtures)."""
    manifest = manifest or MANIFEST
    extra_get = extra_get or {}
    port = free_port()

    class H(__import__("http.server", fromlist=["BaseHTTPRequestHandler"]).BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path in extra_get:
                payload = json.dumps(extra_get[self.path]).encode()
            elif self.path == "/.well-known/agent-hub.json":
                payload = json.dumps(manifest).encode()
            elif self.path == "/ledger":
                payload = json.dumps({"ledger": ledger, "totals": {
                    "total_volume": round(sum(e["amount"] for e in ledger), 2),
                    "total_hub_fees": round(sum(e["hub_fee"] for e in ledger), 2)}}).encode()
            else:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            # C7 (M7): a manifest that declares the mirror must GATE it
            if self.path == "/admin/sync-escrow":
                self.send_response(403)
                payload = json.dumps({"error": "admin key required (X-Admin-Key)"}).encode()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_response(404)
            self.end_headers()

    srv = __import__("http.server", fromlist=["ThreadingHTTPServer"]).ThreadingHTTPServer(
        ("127.0.0.1", port), H)
    __import__("threading").Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{port}"


def main():
    real = None
    try:
        # D1: real hub, real booking -> CONFORMANT
        real, base, state = start_real_hub(with_traffic=True)
        rc, verdict, out = run_check(base)
        check("D1: real hub with booking -> CONFORMANT", rc == 0 and verdict == "CONFORMANT",
              f"rc={rc} {out[-400:]}")

        # D2: fresh hub, empty ledger -> INSUFFICIENT-EVIDENCE
        real.send_signal(signal.SIGTERM)
        real.wait(timeout=10)
        if os.path.exists(state):
            os.remove(state)
        real, base, state = start_real_hub(with_traffic=False)
        rc, verdict, out = run_check(base)
        check("D2: empty ledger -> INSUFFICIENT-EVIDENCE", rc == 0 and verdict == "INSUFFICIENT-EVIDENCE",
              f"rc={rc} {out[-300:]}")
    finally:
        if real:
            real.send_signal(signal.SIGTERM)
            real.wait(timeout=10)

    good = [{"booking": "bk-1", "amount": 10.0, "hub_fee": 0.1, "owner_payout": 9.9, "escrow": "RELEASED"},
            {"booking": "bk-2", "amount": 5.5, "hub_fee": 0.06, "owner_payout": 5.44, "escrow": "HELD"}]

    srv, url = make_stub(good)
    rc, verdict, out = run_check(url)
    check("D0: consistent fixture -> CONFORMANT (control)", rc == 0 and verdict == "CONFORMANT")
    srv.shutdown()

    tampered = [dict(good[0]), {**good[1], "owner_payout": 9.99}]  # 0.06+9.99 != 5.5
    srv, url = make_stub(tampered)
    rc, verdict, out = run_check(url)
    check("D3: tampered arithmetic -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    fee_mismatch = [{**good[0], "hub_fee": 1.0, "owner_payout": 9.0}]  # 10% charged, 1% declared
    srv, url = make_stub(fee_mismatch)
    rc, verdict, out = run_check(url)
    check("D4: fee mismatch vs declared -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    # D5: lying totals - stub with custom totals handler
    port = free_port()
    lying = {"ledger": good, "totals": {"total_volume": 1.0, "total_hub_fees": 0.0}}
    srv, url = make_stub(good)  # placeholder replaced below
    srv.shutdown()
    import http.server
    import threading
    class LyingH(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def do_GET(self):
            payload = json.dumps(MANIFEST).encode() if "well-known" in self.path \
                else json.dumps(lying).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", port), LyingH)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    rc, verdict, out = run_check(f"http://127.0.0.1:{port}")
    check("D5: lying totals -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    weird = [dict(good[0]), {**good[1], "escrow": "MAGICAL"}]
    srv, url = make_stub(weird)
    rc, verdict, out = run_check(url)
    check("D6: unknown escrow state -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    # ---- C6: auth advertising vs challenge contract ----
    MAN_AUTH = {**MANIFEST, "auth": {"kind": "crypto-accounts",
                                     "challenge": "/auth/challenge",
                                     "signup": "/accounts/signup"}}
    CH_OK = {"algo": "sha256-leading-zeros", "challenge": "c" * 32,
             "difficulty": 18, "ttl": 600}

    srv, url = make_stub(good, manifest=MAN_AUTH, extra_get={"/auth/challenge?kind=signup": CH_OK})
    rc, verdict, out = run_check(url)
    check("D7: accounts advertised + valid challenge -> CONFORMANT", rc == 0 and verdict == "CONFORMANT", out[-200:])
    srv.shutdown()

    srv, url = make_stub(good, manifest=MAN_AUTH)  # challenge endpoint 404
    rc, verdict, out = run_check(url)
    check("D8: accounts advertised + challenge 404 -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    srv, url = make_stub(good, manifest=MAN_AUTH, extra_get={"/auth/challenge?kind=signup": {"oops": True}})
    rc, verdict, out = run_check(url)
    check("D9: malformed challenge -> NON-CONFORMANT", rc == 1 and verdict == "NON-CONFORMANT")
    srv.shutdown()

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("D3_ALL_PASSED")


if __name__ == "__main__":
    main()
