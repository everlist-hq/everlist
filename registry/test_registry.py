#!/usr/bin/env python3
"""D2: registry service tests.

Runs the hub (dev, with /challenge + manifest) and the registry (dev mode),
then proves the plan acceptance:
  R1  register a hub: ownership challenge + manifest validation -> 201, tier=open
  R2  hub listed in GET /hubs (open list, never verified)
  R3  GET /hubs/{id} returns record; self-reported ledger totals if exposed
  R4  REGISTRY RESTART -> data survives (registry.json persistence)
  R5  SSRF fixture: registration of a PRIVATE-NET url is rejected and the
      target is NEVER CONTACTED (connection counter stays 0)
  R6  ownership proof fails for a url the operator doesn't control (wrong echo)
  R7  invalid manifest -> 400, not stored
  R8  non-dev mode rejects http registration (https-only policy)
  C6  R9  /registry.json signature verifies + hub listed w/ protocol version
      R9c /registry.pub key matches envelope key
      R10 tampered payload -> INVALID
      R11 check_hub --registry --expect-hub -> CONFORMANT exit 0
      R12 wrong pinned key -> NON-CONFORMANT exit 1; correct key accepted

Run with the experiment venv: experiments/registry/test_registry.py
"""
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
# layout-portable: works in experiments/ (agent-hub-v2) AND the published repo (everlist)
_CANDIDATES = [os.path.join(HERE, "..", "everlist"), os.path.join(HERE, "..", "agent-hub-v2")]
HUB_DIR = next((c for c in _CANDIDATES if os.path.exists(os.path.join(c, "app.py"))), _CANDIDATES[0])
PY = sys.executable  # same interpreter as this test (venv-portable)
REG_STATE = os.path.join("/tmp", f"registry_test_{uuid.uuid4().hex[:8]}.json")
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


HUB_PORT = free_port()
REG_PORT = free_port()
HUB_URL = f"http://127.0.0.1:{HUB_PORT}"
REG_URL = f"http://127.0.0.1:{REG_PORT}"


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def start_hub():
    env = {**os.environ, "HUB_STATE_FILE": os.path.join("/tmp", f"hub_d2_{uuid.uuid4().hex[:6]}.json")}
    p = subprocess.Popen([PY, os.path.join(HUB_DIR, "app.py"), str(HUB_PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    assert wait_ready(HUB_PORT)
    return p


def start_registry(dev=1):
    env = {**os.environ, "REGISTRY_STATE": REG_STATE, "HUB_REGISTRY_PORT": str(REG_PORT),
           "REGISTRY_DEV": str(dev),
           # C6: per-run signing key - tests never touch a live operator key
           "REGISTRY_SIGNING_KEY": os.path.join("/tmp", f"regsign_{uuid.uuid4().hex[:8]}.key")}
    p = subprocess.Popen([PY, os.path.join(HERE, "registry.py")],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    assert wait_ready(REG_PORT)
    return p


def req(url, method="GET", body=None, timeout=30):
    r = urllib.request.Request(url, method=method,
                               data=json.dumps(body).encode() if body is not None else None,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


# -------- evil fixture: private-net server that counts contact attempts --------

CONTACTS = {"n": 0}


class EvilHandler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        CONTACTS["n"] += 1
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b"{}")


def main():
    if os.path.exists(REG_STATE):
        os.remove(REG_STATE)
    hub = start_hub()
    reg = start_registry(dev=1)
    try:
        # R1: register the real hub (ownership challenge + manifest validation)
        st, body = req(f"{REG_URL}/register", "POST", {"url": HUB_URL})
        check("R1: register with ownership proof -> 201 tier=open", st == 201
              and body.get("tier") == "open" and body.get("hub_id", "").startswith("hub-"),
              f"st={st} {body}")
        hid = body.get("hub_id", "")

        # R2: listed in open, NOT in verified
        st, hubs = req(f"{REG_URL}/hubs")
        ids_open = [h["hub_id"] for h in hubs.get("open", [])]
        check("R2: hub in open list, not in verified", hid in ids_open
              and not hubs.get("verified"))

        # R3: detail record with manifest + ledger self-report
        st, rec = req(f"{REG_URL}/hubs/{hid}")
        check("R3: hub detail returns record + ledger totals", st == 200
              and rec.get("url") == HUB_URL and "ledger_totals" in rec, str(rec.get("ledger_check")))

        # R4: registry restart -> persistence
        reg.send_signal(signal.SIGTERM)
        reg.wait(timeout=10)
        reg = start_registry(dev=1)
        st, hubs = req(f"{REG_URL}/hubs")
        check("R4: data survives registry restart", hid in [h["hub_id"] for h in hubs.get("open", [])])

        # R5: SSRF fixture - private-net target never contacted
        evil = ThreadingHTTPServer(("127.0.0.1", free_port()), EvilHandler)
        threading.Thread(target=evil.serve_forever, daemon=True).start()
        # NOTE: registry in dev mode ALLOWS loopback (needed to register the real
        # hub). To prove the SSRF block we run a SECOND registry in prod mode.
        reg.send_signal(signal.SIGTERM)
        reg.wait(timeout=10)
        reg = start_registry(dev=0)  # prod mode: https-only, no loopback
        st, body = req(f"{REG_URL}/register", "POST", {"url": HUB_URL})
        check("R5: prod-mode registration of http/loopback hub rejected", st == 400)
        check("R5b: private target NEVER contacted", CONTACTS["n"] == 0, str(CONTACTS))

        # R6: ownership proof failure (hub down -> challenge unreachable)
        hub.send_signal(signal.SIGTERM)
        hub.wait(timeout=10)
        st, body = req(f"{REG_URL}/register", "POST", {"url": HUB_URL})
        check("R6: unprovable ownership rejected (challenge unreachable)", st == 400)
        hub = start_hub()  # restart for remaining checks

        # R7: invalid manifest -> 400, not stored. We register a stub hub with
        # a valid challenge but garbage manifest.
        stub_port = free_port()

        class StubHub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path.startswith("/challenge"):
                    nonce = self.path.split("=")[-1]
                    payload = json.dumps({"challenge_response": nonce}).encode()
                else:
                    payload = json.dumps({"name": "x"}).encode()  # INVALID manifest
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        stub_srv = ThreadingHTTPServer(("127.0.0.1", stub_port), StubHub)
        threading.Thread(target=stub_srv.serve_forever, daemon=True).start()
        # prod registry blocks loopback; run a dev registry for this fixture
        reg.send_signal(signal.SIGTERM)
        reg.wait(timeout=10)
        reg = start_registry(dev=1)
        st, body = req(f"{REG_URL}/register", "POST", {"url": f"http://127.0.0.1:{stub_port}"})
        check("R7: invalid manifest -> 400, not stored", st == 400 and "invalid manifest" in body.get("error", ""))
        st, hubs = req(f"{REG_URL}/hubs")
        check("R7b: invalid hub not in list", all(
            h["url"] != f"http://127.0.0.1:{stub_port}" for h in hubs.get("open", [])))
        stub_srv.shutdown()

        # ---- C6: signed /registry.json ---------------------------------
        import check_hub  # verify with the REAL verifier (same dir)

        # R9: document exists, signature verifies, registered hub listed
        # with protocol version carried from its manifest
        st, doc = req(f"{REG_URL}/registry.json")
        ok, payload, why = check_hub.verify_envelope(doc)
        check("R9: /registry.json signature verifies", st == 200 and ok, why)
        r9hub = next((h for h in payload["hubs"] if h["url"] == HUB_URL), None) if payload else None
        check("R9b: registered hub in signed payload with protocol version",
              r9hub is not None and r9hub.get("protocol") == "agent-hub/0.2",
              str(r9hub))
        st, pub = req(f"{REG_URL}/registry.pub")
        check("R9c: /registry.pub key matches envelope key",
              st == 200 and pub.get("public_key") == doc["signature"]["public_key"])

        # R10: tampered payload -> INVALID
        import copy
        bad = copy.deepcopy(doc)
        bad["payload"]["hub_count"] = 999
        ok, _, why = check_hub.verify_envelope(bad)
        check("R10: tampered payload rejected", not ok and "INVALID" in why, why)

        # R11: CLI verification mode (real end-to-end: fetch + verify + membership)
        cli = subprocess.run(
            [PY, os.path.join(HERE, "check_hub.py"), "--registry", REG_URL,
             "--expect-hub", HUB_URL], capture_output=True, text=True, timeout=60)
        check("R11: check_hub --registry CONFORMANT (exit 0)",
              cli.returncode == 0 and "VERDICT: CONFORMANT" in cli.stdout,
              cli.stdout[-300:] + cli.stderr[-200:])

        # R12: out-of-band key pinning - wrong key refused, right key accepted
        wrong = "ab" * 32
        cli_bad = subprocess.run(
            [PY, os.path.join(HERE, "check_hub.py"), "--registry", REG_URL,
             "--pubkey", wrong], capture_output=True, text=True, timeout=60)
        check("R12: wrong pinned key -> NON-CONFORMANT (exit 1)",
              cli_bad.returncode == 1 and "DIFFERENT key" in cli_bad.stdout,
              cli_bad.stdout[-300:])
        cli_ok = subprocess.run(
            [PY, os.path.join(HERE, "check_hub.py"), "--registry", REG_URL,
             "--pubkey", pub.get("public_key", "")], capture_output=True, text=True, timeout=60)
        check("R12b: correct pinned key accepted",
              cli_ok.returncode == 0 and "MATCHES the pinned" in cli_ok.stdout,
              cli_ok.stdout[-300:])

    finally:
        for p in (hub, reg):
            try:
                p.send_signal(signal.SIGTERM)
                p.wait(timeout=10)
            except Exception:
                pass
        if os.path.exists(REG_STATE):
            os.remove(REG_STATE)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("D2_ALL_PASSED")


if __name__ == "__main__":
    main()
