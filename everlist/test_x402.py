#!/usr/bin/env python3
"""C3a: real x402 (EIP-3009) verification tests for agent-hub-v2.

Runs the hub with HUB_PAY_MODE=testnet and proves:
  P1  real signed testnet payment verifies
  N1  tampered authorization (value changed after signing) fails
  N2  wrong recipient fails
  N3  insufficient amount fails
  N4  expired authorization fails
  N5  wrong network fails
  N6  replayed payment fails (same nonce, same server run)
  N7  replay across server RESTART fails (nonce registry persisted in state)

Style follows test_suite.py: own server, own state file, PASS/FAIL summary,
exit 1 on any failure. Run with the experiment venv (needs eth-account).
"""
import base64
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

from eth_account import Account

HERE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(HERE, "venv", "bin", "python")
STATE = os.path.join("/tmp", f"hub_c3a_{uuid.uuid4().hex[:8]}.json")

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


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def start_server():
    env = {**os.environ, "HUB_PAY_MODE": "testnet", "HUB_STATE_FILE": STATE}
    p = subprocess.Popen([PY, os.path.join(HERE, "app.py"), str(PORT)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)
    end = time.time() + 15
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return p
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("server not ready")


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


def build_payment(terms, acct, *, to=None, value=None, nonce=None,
                  valid_after=None, valid_before=None, network="base-sepolia"):
    """Build a REAL signed EIP-3009 x402 X-PAYMENT header from 402 terms."""
    acc = terms["accepts"][0]
    now = int(time.time())
    auth = {
        "from": acct.address,
        "to": to if to is not None else acc["payTo"],
        "value": value if value is not None else int(acc["maxAmountRequired"]),
        "validAfter": valid_after if valid_after is not None else now - 60,
        "validBefore": valid_before if valid_before is not None else now + 600,
        "nonce": nonce if nonce is not None else "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:32],
    }
    domain = {"name": "USD Coin", "version": "2", "chainId": 84532,
              "verifyingContract": acc["asset"]}
    types = {"TransferWithAuthorization": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]}
    signed = acct.sign_typed_data(domain_data=domain, message_types=types, message_data=auth)
    payload = {"scheme": "exact", "network": network, "x402Version": 1,
               "payload": {"authorization": auth, "signature": signed.signature.hex()}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


def main():
    if os.path.exists(STATE):
        os.remove(STATE)
    srv = start_server()
    p1_header = None
    try:
        acct = Account.from_key(os.urandom(32))

        st, terms = req("GET", "/premium/events")
        check("P0: 402 terms with accepts", st == 402 and terms.get("accepts"))
        check("P0b: mode label is TESTNET", terms.get("mode") == "TESTNET", str(terms.get("mode")))

        # P1: real signed payment (captured for N6/N7 replay)
        p1_header = build_payment(terms, acct)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": p1_header})
        check("P1: real signed payment verifies (200)", st == 200, f"st={st} {body}")
        check("P1b: verified=true + settlement honestly pending",
              body.get("payment", {}).get("verified") is True
              and "settled" not in str(body.get("payment", {}).get("settlement", "")).lower())

        req_amt = int(terms["accepts"][0]["maxAmountRequired"])

        # N1: tampered value after signing (even if still >= required)
        pay = build_payment(terms, acct)
        raw = json.loads(base64.b64decode(pay))
        raw["payload"]["authorization"]["value"] = req_amt + 1_000_000
        st, body = req("GET", "/premium/events", None,
                       {"X-PAYMENT": base64.b64encode(json.dumps(raw).encode()).decode()})
        check("N1: tampered value fails", st == 402, f"st={st}")

        # N2: wrong recipient (consistently signed by payer)
        rogue = Account.from_key(os.urandom(32))
        pay = build_payment(terms, acct, to=rogue.address)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay})
        check("N2: wrong recipient fails", st == 402, f"st={st}")

        # N3: insufficient amount
        pay = build_payment(terms, acct, value=req_amt - 1)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay})
        check("N3: insufficient amount fails", st == 402, f"st={st}")

        # N4: expired
        now = int(time.time())
        pay = build_payment(terms, acct, valid_after=now - 7200, valid_before=now - 3600)
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay})
        check("N4: expired authorization fails", st == 402, f"st={st}")

        # N5: wrong network
        pay = build_payment(terms, acct, network="base")
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": pay})
        check("N5: wrong network fails", st == 402, f"st={st}")

        # N6: replay P1 on the same server run
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": p1_header})
        check("N6: replayed payment fails (same run)", st == 402, f"st={st}")

        # N7: restart persistence -> replay P1 across restart
        srv.send_signal(signal.SIGTERM)
        srv.wait(timeout=10)
        srv = start_server()
        st, body = req("GET", "/premium/events", None, {"X-PAYMENT": p1_header})
        check("N7: replay across restart fails (nonce persisted)", st == 402, f"st={st} {body}")

    finally:
        try:
            srv.send_signal(signal.SIGTERM)
            srv.wait(timeout=10)
        except Exception:
            pass
        if os.path.exists(STATE):
            os.remove(STATE)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)
    print("C3A_ALL_PASSED")


if __name__ == "__main__":
    main()
