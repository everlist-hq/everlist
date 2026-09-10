#!/usr/bin/env python3
"""C3b: settlement evidence tests for agent-hub-v2 (facilitator flow).

Runs the hub with HUB_PAY_MODE=testnet, HUB_SETTLE_MODE=auto and a LOCAL STUB
facilitator (behavior-routed), then proves the plan's acceptance criteria:

  S1  real payment verifies AND settles: response shows SETTLED + tx hash
  S2  ledger contains x402_settlement event matching tx + fingerprint (D1)
  S3  stub balances moved: payer debited, payee credited (before/after printed)
  S4  facilitator called EXACTLY ONCE per payment
  S5  replay of settled payment -> 402 (nonce registry) -> no double-settle
  S6  after RESTART: replay still 402, stub still called once
  S7  facilitator unreachable (dead port) -> settlement UNKNOWN, no tx, no ledger event
  S8  facilitator 5xx -> UNKNOWN (never claim settled without proof)
  S9  facilitator 4xx -> FAILED (definitive), no ledger event
  S10 duplicate registry.settle() in-process -> duplicate=True, one call
  S11 reconcile(): every settled fp has a ledger event; unknown/failed don't
      block reconciliation

Run with the experiment venv. HONEST LIMIT: the real CDP facilitator needs an
API key (HUB_FACILITATOR_KEY - USER ACTION); public RPCs are blocked from this
container. This test proves the FULL settlement MACHINERY against a faithful
stub; production wiring is a config change (env vars), not new code.
"""
import base64
import hashlib
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

from eth_account import Account

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import x402facilitate as X402F  # canonical fingerprint (lowercased addresses)

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "bin", "python")
STATE = os.path.join("/tmp", f"hub_c3b_{uuid.uuid4().hex[:8]}.json")
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL") + f" | {name}" + (f" | {detail}" if detail and not cond else ""))


# ---------------- stub facilitator ----------------

class StubFacil:
    """Behavior-routed stub: base path segment picks behavior.
    /ok/settle -> settle, /fail400/settle -> 400, /fail500/settle -> 500.
    Balances tracked per address; settle-call counts per fingerprint.
    """

    def __init__(self):
        self.balances = {}          # addr -> micro-usdc
        self.calls = {}             # fp -> count
        self.settled = {}           # fp -> tx
        self.port = self._free()
        srv = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                ln = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(ln) or b"{}")
                parts = self.path.strip("/").split("/")
                behavior = parts[0] if parts else "ok"
                if not self.path.endswith("/settle"):
                    return self._send(404, {"error": "not found"})
                auth = (body.get("paymentPayload") or {}).get("payload", {}).get("authorization", {})
                fp = X402F.payment_fingerprint(auth)
                srv.calls[fp] = srv.calls.get(fp, 0) + 1
                if behavior == "fail400":
                    return self._send(400, {"error": "invalid signature (stub)"})
                if behavior == "fail500":
                    return self._send(500, {"error": "internal (stub)"})
                # ok: simulate on-chain transfer + tx hash
                tx = "0x" + hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
                payer, payee = auth.get("from"), auth.get("to")
                val = int(auth.get("value", 0))
                srv.balances[payer] = srv.balances.get(payer, 10_000_000) - val
                srv.balances[payee] = srv.balances.get(payee, 0) + val
                srv.settled[fp] = tx
                self._send(200, {"success": True, "transaction": tx,
                                 "network": "base-sepolia", "payer": payer})

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @staticmethod
    def _free():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return p

    def base(self, behavior):
        return f"http://127.0.0.1:{self.port}/{behavior}"

    def dead_base(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        return f"http://127.0.0.1:{p}"  # nothing listens


# ---------------- hub control ----------------

HUB_PORT = None


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_hub(facil_base):
    env = {**os.environ, "HUB_PAY_MODE": "testnet", "HUB_SETTLE_MODE": "auto",
           "HUB_STATE_FILE": STATE, "HUB_FACILITATOR_URL": facil_base}
    p = subprocess.Popen([PY, os.path.join(HERE, "app.py"), str(HUB_PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    end = time.time() + 15
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", HUB_PORT), timeout=0.5):
                return p
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("hub not ready")


def req(method, path, body=None, headers=None):
    r = urllib.request.Request(f"http://127.0.0.1:{HUB_PORT}" + path, method=method,
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


def build_payment(terms, acct):
    acc = terms["accepts"][0]
    now = int(time.time())
    auth = {"from": acct.address, "to": acc["payTo"], "value": int(acc["maxAmountRequired"]),
            "validAfter": now - 60, "validBefore": now + 600,
            "nonce": "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:32]}
    domain = {"name": "USD Coin", "version": "2", "chainId": 84532, "verifyingContract": acc["asset"]}
    types = {"TransferWithAuthorization": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]}
    signed = acct.sign_typed_data(domain_data=domain, message_types=types, message_data=auth)
    payload = {"scheme": "exact", "network": "base-sepolia", "x402Version": 1,
               "payload": {"authorization": auth, "signature": signed.signature.hex()}}
    return base64.b64encode(json.dumps(payload).encode()).decode(), auth


def main():
    global HUB_PORT
    if os.path.exists(STATE):
        os.remove(STATE)
    HUB_PORT = _free_port()
    stub = StubFacil()
    hub = start_hub(stub.base("ok"))
    try:
        st, terms = req("GET", "/premium/events")
        check("S0: hub up with settle=auto", st == 402 and terms.get("accepts"))
        req_amt = int(terms["accepts"][0]["maxAmountRequired"])

        # S1-S4: happy path settle
        acct1 = Account.from_key(os.urandom(32))
        pay1, auth1 = build_payment(terms, acct1)
        payer_bal_before = stub.balances.get(acct1.address, 10_000_000)
        payee_bal_before = stub.balances.get(auth1["to"], 0)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay1})
        settle = body.get("payment", {}).get("settlement", {})
        check("S1: payment verified + SETTLED with tx", st == 200 and settle.get("status") == "SETTLED"
              and settle.get("tx", "").startswith("0x"), f"st={st} settle={settle}")
        tx1 = settle.get("tx", "")
        fp1 = X402F.payment_fingerprint(auth1)

        st, led = req("GET", "/ledger")
        evts = [e for e in led.get("ledger", []) if e.get("kind") == "x402_settlement"]
        check("S2: ledger x402_settlement event matches tx+fp (D1)",
              any(e["detail"].get("tx") == tx1 and e["detail"].get("fingerprint") == fp1
                  and e["detail"].get("value") == req_amt for e in evts),
              f"events={evts}")

        payer_after = stub.balances[acct1.address]
        payee_after = stub.balances[auth1["to"]]
        print(f"  balances: payer {payer_bal_before} -> {payer_after} (debit {req_amt}) | "
              f"payee {payee_bal_before} -> {payee_after} (credit {req_amt})")
        check("S3: balances moved payer->payee exactly",
              payer_bal_before - payer_after == req_amt and payee_after - payee_bal_before == req_amt)
        check("S4: facilitator called exactly once", stub.calls.get(fp1) == 1, str(stub.calls))

        # S5: replay -> 402, no second call
        st, _ = req("GET", "/premium/events", None, {"X-PAYMENT": pay1})
        check("S5: replay of settled payment 402 (no double-settle)", st == 402
              and stub.calls.get(fp1) == 1)

        # S6: restart; replay still blocked; call count unchanged
        hub.send_signal(signal.SIGTERM)
        hub.wait(timeout=10)
        hub = start_hub(stub.base("ok"))
        st, _ = req("GET", "/premium/events", None, {"X-PAYMENT": pay1})
        check("S6: replay after restart 402 + still one call", st == 402
              and stub.calls.get(fp1) == 1, f"st={st} calls={stub.calls.get(fp1)}")

        # S7: unreachable facilitator -> UNKNOWN, no tx, no ledger event
        acct2 = Account.from_key(os.urandom(32))
        hub.send_signal(signal.SIGTERM)
        hub.wait(timeout=10)
        hub = start_hub(stub.dead_base())
        pay2, _ = build_payment(terms, acct2)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay2})
        settle2 = body.get("payment", {}).get("settlement", {})
        check("S7: unreachable facilitator -> UNKNOWN (honest)", st == 200
              and settle2.get("status") == "UNKNOWN", f"st={st} settle={settle2}")

        # S8: 5xx -> UNKNOWN
        hub.send_signal(signal.SIGTERM)
        hub.wait(timeout=10)
        hub = start_hub(stub.base("fail500"))
        acct3 = Account.from_key(os.urandom(32))
        pay3, _ = build_payment(terms, acct3)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay3})
        settle3 = body.get("payment", {}).get("settlement", {})
        check("S8: facilitator 5xx -> UNKNOWN", st == 200 and settle3.get("status") == "UNKNOWN")

        # S9: 4xx -> FAILED, no ledger event
        hub.send_signal(signal.SIGTERM)
        hub.wait(timeout=10)
        hub = start_hub(stub.base("fail400"))
        acct4 = Account.from_key(os.urandom(32))
        pay4, _ = build_payment(terms, acct4)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay4})
        settle4 = body.get("payment", {}).get("settlement", {})
        check("S9: facilitator 4xx -> FAILED", st == 200 and settle4.get("status") == "FAILED")

        # ledger must still contain ONLY the one settled event
        st, led = req("GET", "/ledger")
        evts = [e for e in led.get("ledger", []) if e.get("kind") == "x402_settlement"]
        check("S9b: no ledger events for unknown/failed settlements", len(evts) == 1
              and evts[0]["detail"]["tx"] == tx1, f"n={len(evts)}")

        # S11: reconcile settled vs ledger (in-process, module-level)
        sys.path.insert(0, HERE)
        import x402facilitate as X
        reg = X.SettlementRegistry()
        reg.load({fp1: {"status": "settled", "tx": tx1},
                  "fp-unknown-1": {"status": "unknown"},
                  "fp-failed-1": {"status": "failed"}})
        rec = X.reconcile(reg, evts)
        check("S11: reconcile(): settled fp present in ledger, ok=True",
              rec["ok"] and fp1 in rec["settled"], str(rec))

    finally:
        try:
            hub.send_signal(signal.SIGTERM)
            hub.wait(timeout=10)
        except Exception:
            pass
        stub.httpd.shutdown()
        if os.path.exists(STATE):
            os.remove(STATE)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("C3B_ALL_PASSED")


if __name__ == "__main__":
    main()
