"""M14: Tier-2 sign-in - the hub accepts a Midnight credential as the
human_verified source (verified_by: midnight-zk), replacing operator vouch
as trust issuer (vouch path unchanged for Tier-1 pilot).

Done-when (backlog): account shows verified_by: midnight-zk; bookings carry
it server-side; old vouch path unchanged.

Verification source honesty: the credential contract's PUBLIC state read via
midnight_credential.CredentialVerifier - chain mode when an indexer URL is
configured, otherwise the recorded timeline of the REAL offline circuit
simulator (credential-scenario.mjs, M13's compiled circuits). Fail-closed on
every error; the mode labels every outcome.

Run: python test_m14_tier2.py
"""
import binascii
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib            # noqa: E402
import midnight_credential  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


TMP = tempfile.mkdtemp(prefix="hub-m14-")
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


# the hub reads THIS file per request; the test mutates it to drive phases
FIX = os.path.join(TMP, "timeline.json")

env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "HUB_CRED_FIXTURE": FIX,
       "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15; ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5): ready = True; break
    except OSError: time.sleep(0.2)
assert ready, "test hub did not start"

ACTIVE_CRED = "1"   # holder H: admitted, proveHolder OK (never revoked in main fixture)
REVOKED_CRED = "2"  # holder R: revoked in the main fixture already


def rewrite_fixture(mutator):
    doc = json.load(open(os.path.join(HERE, "fixtures", "credential-timeline.json")))
    mutator(doc)
    json.dump(doc, open(FIX, "w"))


def revoke_in_latest(doc):
    """Append an honest revokeCredential action: latest state marks cred 1 revoked."""
    last = json.loads(binascii.unhexlify(doc["actions"][-1]["state_hex"]).decode())
    last["revoked"]["1"] = True
    doc["actions"].append({"entry_point": "revokeCredential", "tx": "f" * 64,
        "state_hex": binascii.hexlify(json.dumps(last).encode()).decode()})


def admit_cred3(doc):
    """Issuer admits a third holder (honest timeline growth): cred 3, active.
    Used as the chat account's credential so API-phase bindings never clash."""
    last = json.loads(binascii.unhexlify(doc["actions"][-1]["state_hex"]).decode())
    last["holders"]["3"] = "ab" * 32
    last["revoked"]["3"] = False
    doc["actions"].append({"entry_point": "issueCredential", "tx": "e" * 64,
        "state_hex": binascii.hexlify(json.dumps(last).encode()).decode()})


CANON = admit_cred3  # the canonical test timeline: cred 1 active, 2 revoked, 3 active


try:
    # canonical test timeline: cred 1 active, 2 revoked, 3 active (chat credential)
    _canon = json.load(open(os.path.join(HERE, "fixtures", "credential-timeline.json")))
    CANON(_canon)
    json.dump(_canon, open(FIX, "w"))

    print("== M14 module: verifier against the REAL sim timeline ==")
    v = midnight_credential.CredentialVerifier("sim-addr", fixture_path=FIX)
    r = v.verify(ACTIVE_CRED)
    check("active credential -> admitted, not revoked", r["admitted"] and r["revoked"] is False)
    check("verified_by is midnight-zk", r["verified_by"] == "midnight-zk")
    check("mode is simulated (honest label)", r["mode"] == "simulated")
    check("evidence tx present", bool(r["evidence_tx"]))
    r = v.verify(REVOKED_CRED)
    check("revoked credential -> admitted but revoked", r["admitted"] and r["revoked"] is True)
    check("revoked credential verifies to None", r["verified_by"] is None)
    r = v.verify("999")
    check("unknown credential -> not admitted", r["admitted"] is False and r["verified_by"] is None)

    def unmark(doc):
        last = json.loads(binascii.unhexlify(doc["actions"][-1]["state_hex"]).decode())
        last["revoked"].pop("1", None)  # corrupted registry: no mark at all
        doc["actions"][-1]["state_hex"] = binascii.hexlify(json.dumps(last).encode()).decode()
    rewrite_fixture(unmark)
    r = v.verify(ACTIVE_CRED)
    check("unmarked credential fails CLOSED (treated revoked)", r["revoked"] is True and r["verified_by"] is None)
    rewrite_fixture(CANON)  # restore exact copy

    print("== M14 API: auth + validation matrix ==")
    c, a = req("POST", "/accounts/signup", {"agent": "m14-holder", "pow": solve_pow("signup")})
    assert c == 201, a
    CODE = a["account_code"]
    c, l = req("POST", "/accounts/login", {"account_code": CODE, "agent": "m14-holder"})
    assert c == 200, l
    TOK = l["tokens"]["list"]
    BTOK = l["tokens"]["book"]
    check("fresh account: verified_by None", l.get("verified_by") is None, str(l.get("verified_by")))

    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED})
    check("no auth -> 403", c == 403, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": "acct-00000000"})
    check("wrong code -> 403", c == 403, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": "", "account_code": CODE})
    check("missing credential_id -> 400", c == 400, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": "-3", "account_code": CODE})
    check("negative credential_id -> 400", c == 400, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": "99999999999999999", "account_code": CODE})
    check("oversized credential_id -> 400", c == 400, "%s %s" % (c, e))
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE,
                "holder_commitment": "zz" * 32})
    check("bad holder_commitment -> 400", c == 400, "%s %s" % (c, e))

    print("== M14 API: the Tier-2 sign-in itself ==")
    c, e = req("POST", "/accounts/verify-midnight", {"credential_id": REVOKED_CRED, "account_code": CODE})
    check("revoked credential -> 403", c == 403, "%s %s" % (c, e))
    check("revoked response honest (revoked: true)", e.get("revoked") is True and e.get("verified_by") is None)
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": "424242", "account_code": CODE})
    check("unknown credential -> 403", c == 403, "%s %s" % (c, w))
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE})
    check("active credential -> 200 Tier-2 verified", c == 200, "%s %s" % (c, w))
    check("verified_by: midnight-zk", w.get("verified_by") == "midnight-zk", str(w.get("verified_by")))
    check("mode labeled (simulated)", w.get("mode") == "simulated")
    check("evidence_tx surfaced", bool(w.get("evidence_tx")))

    c, l2 = req("POST", "/accounts/login", {"account_code": CODE, "agent": "m14-holder2"})
    check("login carries verified_by midnight-zk", l2.get("verified_by") == "midnight-zk")
    check("login carries credential binding", l2.get("midnight_credential") == 1, str(l2.get("midnight_credential")))
    state = json.load(open(os.path.join(TMP, "state.json")))
    acct = next(v for v in state["accounts"].values() if v.get("midnight_credential") == 1)
    check("persisted: verified_by midnight-zk", acct.get("verified_by") == "midnight-zk")
    check("persisted: human_verified true", acct.get("human_verified") is True)

    print("== M14 API: anti-sybil binding (1:1 credential<->account) ==")
    c, b = req("POST", "/accounts/signup", {"agent": "m14-second", "pow": solve_pow("signup")})
    assert c == 201, b
    CODE2 = b["account_code"]
    AID2 = b["account_id"]
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE2})
    check("credential reuse on 2nd account -> 409", c == 409, "%s %s" % (c, w))
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE})
    check("re-verify same account+credential -> 200 (idempotent)", c == 200, "%s %s" % (c, w))

    print("== M14 bookings carry provenance (server-side only) ==")
    c, ls = req("POST", "/listings", {"vertical": "events", "title": "M14 Gig", "date": "2026-10-10",
                "location": "X", "price": 0, "capacity": 10}, {"X-Hub-Token": TOK})
    assert c == 201, ls
    LID = ls["id"]
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "M14 Buyer",
               "verified_by": "midnight-zk"}, {"X-Hub-Token": BTOK})
    check("client-injected verified_by rejected (reserved)", c == 400, "%s %s" % (c, w))
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "M14 Buyer"}, {"X-Hub-Token": BTOK})
    check("verified account books with no client flag", c == 201, "%s %s" % (c, w))
    check("booking carries verified_by: midnight-zk server-side", w.get("verified_by") == "midnight-zk",
          str(w.get("verified_by")))

    print("== M14 revocation propagates (downgrade) ==")
    rewrite_fixture(revoke_in_latest)
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE})
    check("revoked-after-binding -> 403", c == 403, "%s %s" % (c, w))
    state = json.load(open(os.path.join(TMP, "state.json")))
    acct = next(v for v in state["accounts"].values() if v.get("midnight_credential") == 1)
    check("account DOWNGRADED: verified_by midnight-zk-revoked", acct.get("verified_by") == "midnight-zk-revoked",
          str(acct.get("verified_by")))
    check("account DOWNGRADED: human_verified false", acct.get("human_verified") is False)
    c, l3 = req("POST", "/accounts/login", {"account_code": CODE, "agent": "m14-holder3"})
    check("login shows revoked provenance", l3.get("verified_by") == "midnight-zk-revoked", str(l3.get("verified_by")))

    print("== M14 fail-closed: verifier unavailable ==")
    open(FIX, "wb").write(b"\x00\xff not json at all")  # corrupt evidence file
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": ACTIVE_CRED, "account_code": CODE2})
    check("broken evidence -> 502 fail-closed", c == 502, "%s %s" % (c, w))
    check("502 never fabricates verification", w.get("verified_by") is None)
    state = json.load(open(os.path.join(TMP, "state.json")))
    acct2 = next(v for v in state["accounts"].values() if v["bound"] == ["m14-second"])
    check("fail-closed mutated nothing", acct2.get("verified_by") is None and acct2.get("human_verified") is False)
    rewrite_fixture(CANON)  # restore

    print("== M14 vouch path unchanged (Tier-1 pilot) ==")
    c, w = req("POST", "/accounts/vouch", {"account_id": AID2},
               {"X-Admin-Key": "dev-admin-key-change-me"})
    check("operator vouch still works", c == 200 and w.get("verified_by") == "admin-vouch", "%s %s" % (c, w))

    print("== M14 chat: verify-midnight E2E ==")
    SENDER = "m14-chat"
    out = chatlib.handle_text(BASE, "verify-midnight", SENDER)
    check("chat gate: login first", "Login first" in out, out[:80])
    out = chatlib.handle_text(BASE, "signup", SENDER)
    check("chat signup ok", "Account created" in out, out[:80])
    out = chatlib.handle_text(BASE, "verify-midnight nope", SENDER)
    check("chat rejects non-numeric credential", "Usage" in out, out[:80])
    out = chatlib.handle_text(BASE, "verify-midnight " + REVOKED_CRED, SENDER)
    check("chat reports revocation honestly", "REVOKED" in out.upper(), out[:100])
    out = chatlib.handle_text(BASE, "verify-midnight 3", SENDER)
    check("chat Tier-2 success (simulated mode labeled)", "verified" in out and "simulated" in out, out[:100])
    check("chat session now verified", chatlib._SESSIONS[SENDER].get("verified") is True)
    check("chat session carries verified_by", chatlib._SESSIONS[SENDER].get("verified_by") == "midnight-zk")
    out = chatlib.handle_text(BASE, "whoami", SENDER)
    check("whoami shows Midnight ZK provenance", "Midnight ZK" in out, out[:100])
    out = chatlib.handle_text(BASE, "help", SENDER)
    check("help lists verify-midnight", "verify-midnight" in out)

    print("== M14 OpenAPI + SPEC ==")
    c, api = req("GET", "/openapi.json")
    check("openapi documents verify-midnight", "/accounts/verify-midnight" in api.get("paths", {}))
finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()

failed = [n for n, ok in RESULTS if not ok]
print("M14: %d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("M14_TIER2_ALL_PASSED (mode: simulated - real circuit-sim timeline; chain mode ready via HUB_CRED_INDEXER)")
