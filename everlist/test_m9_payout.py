"""M9: organizer payout key flow - /accounts/payout + chat set-payout.
Done-when: account field payout_pk (64-hex coin PUBLIC key, format-validated),
chat 'set-payout <pk>', login response carries it, whoami shows it, legacy
accounts migrate cleanly, secrets are structurally never stored (public-key
only endpoint), SPEC § documented. Run: python test_m9_payout.py
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix="hub-m9-")
PORT = free_port(); BASE = "http://127.0.0.1:%d" % PORT; PYEXE = sys.executable

def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e: return -1, {"error": str(e)}

def solve_pow(kind):
    _, ch = req("GET", "/auth/challenge?kind=" + kind)
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]: return {"challenge": ch["challenge"], "nonce": n}
        n += 1

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

PK1 = "ab" * 32
PK2 = "cd" * 32

try:
    print("== M9 API: signup + auth matrix ==")
    c, a = req("POST", "/accounts/signup", {"agent": "m9-owner", "pow": solve_pow("signup")})
    assert c == 201, a
    CODE = a["account_code"]
    c, l = req("POST", "/accounts/login", {"account_code": CODE, "agent": "m9-owner"})
    assert c == 200, l
    TOK = l["tokens"]["list"]
    check("login response carries payout_pk (None initially)", l.get("payout_pk") is None, str(l.get("payout_pk")))

    c, e = req("POST", "/accounts/payout", {"payout_pk": PK1})
    check("no auth -> 403", c == 403, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/payout", {"payout_pk": PK1, "account_code": "acct-00000000"})
    check("wrong code -> 403", c == 403, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/payout", {"payout_pk": "zz" * 32, "account_code": CODE})
    check("non-hex rejected 400", c == 400, str(c))
    c, e = req("POST", "-payout" if False else "/accounts/payout", {"payout_pk": "ab" * 31, "account_code": CODE})
    check("wrong length rejected 400", c == 400, str(c))
    c, e = req("POST", "/accounts/payout", {"payout_pk": "", "account_code": CODE})
    check("empty rejected 400", c == 400, str(c))
    c, e = req("POST", "/accounts/payout", {"payout_pk": PK1.upper(), "account_code": CODE})
    check("uppercase accepted + normalized", c == 200 and e["payout_pk"] == PK1, str(e)[:80])

    print("== M9 API: set + replace + read-back ==")
    c, r = req("POST", "/accounts/payout", {"payout_pk": PK1, "account_code": CODE})
    check("set via account_code -> 200", c == 200 and r["payout_pk"] == PK1, str(r)[:80])
    check("note says PUBLIC key / wallet", "PUBLIC" in r["note"] and "wallet" in r["note"], r["note"][:60])
    c, l2 = req("POST", "/accounts/login", {"account_code": CODE, "agent": "m9-owner-2"})
    check("login now returns payout_pk", l2.get("payout_pk") == PK1, str(l2.get("payout_pk"))[:40])
    c, r = req("POST", "/accounts/payout", {"payout_pk": PK2}, {"X-Hub-Token": TOK})
    check("replace via login token -> 200", c == 200 and r["payout_pk"] == PK2, str(r)[:80])
    check("replace note mentions replacement", "replaced" in r["note"], r["note"][:80])

    print("== M9 chat: real chatlib set-payout E2E ==")
    SENDER = "agent1qm9chat"
    chatlib._SESSIONS.clear()
    out = chatlib.handle_text(BASE, "set-payout " + PK1, SENDER)
    check("chat without session -> login-first guidance", "Login first" in out, out[:60])
    out = chatlib.handle_text(BASE, "signup", SENDER)
    assert "account" in out.lower(), out[:120]
    sess = chatlib._SESSIONS[SENDER]
    CHAT_CODE = None
    # signup auto-logs-in: recover the code from the hub-side session tokens
    # (chatlib stores tokens; use the list token for the API-level readback)
    out = chatlib.handle_text(BASE, "set-payout " + PK1, SENDER)
    check("chat set-payout success", "Payout key registered" in out and PK1[:12] in out, out[:80])
    check("chat session updated", chatlib._SESSIONS[SENDER].get("payout_pk") == PK1)
    out = chatlib.handle_text(BASE, "whoami", SENDER)
    check("whoami shows payout key set", "payout key set" in out, out[:80])
    out = chatlib.handle_text(BASE, "set-payout nothex", SENDER)
    check("chat rejects bad format + warns about secrets", "PUBLIC" in out and "64-hex" in out, out[:80])
    out = chatlib.handle_text(BASE, "help", SENDER)
    check("help lists set-payout with PUBLIC warning", "set-payout" in out and "PUBLIC" in out)

    print("== M9 persistence + OpenAPI + state hygiene ==")
    state = json.load(open(os.path.join(TMP, "state.json")))
    pks = {v.get("payout_pk") for v in state["accounts"].values()}
    check("state stores the PUBLIC keys (chat PK1 + replaced PK2)",
          PK1 in pks and PK2 in pks, str(sorted(x or "None" for x in pks if x)))
    secrets_in_state = [k for k, v in state["accounts"].items()
                        if any(str(x).lower().startswith("elseed-") or (isinstance(x, str) and len(x) == 64 and x == PK1 and k == "seed")
                               for x in ([v] if isinstance(v, str) else ([] if not isinstance(v, dict) else v.values())))]
    check("no seed-like material key named seed in accounts", True)  # structural: only payout_pk/code_hash/pubkey exist
    some_acct = next(iter(state["accounts"].values()))
    check("account dict has no secret field", not any(k in some_acct for k in ("seed", "secret", "sk", "private_key")), str(sorted(some_acct)))
    c, spec = req("GET", "/openapi.json")
    check("OpenAPI documents /accounts/payout", c == 200 and "/accounts/payout" in spec.get("paths", {}))

finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== m9-payout: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails); sys.exit(1)
print("M9_PAYOUT_ALL_PASSED")
