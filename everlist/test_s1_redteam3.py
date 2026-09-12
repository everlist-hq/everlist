"""S-track red-team pass 3 (S1/S2): locks the four recon fixes.

F1  escrow_ref uniqueness wall (one chain escrow = one hub booking) + Uint<64> bound
F2  payout limits moved PRE-AUTH (brute-forceable secrets count toward limits)
F3  rating route gets a per-source backstop
F4  dev admin key warns loudly on stderr when HUB_ENV is not production

Run: python test_s1_redteam3.py
"""
import atexit
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
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, "" if cond else detail)


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def req(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(BASE + path,
        data=(json.dumps(body).encode() if body is not None else None),
        method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


def solve_pow(kind):
    _, ch = req("GET", "/auth/challenge?kind=" + kind)
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0:
                bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


TMP = tempfile.mkdtemp(prefix="hub-s1-")
PORT = free_port()
BASE = "http://127.0.0.1:%d" % PORT
ERRPATH = os.path.join(TMP, "hub.err")

env = {**os.environ,
       "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8",
       "HUB_LIMIT_PAYOUT": "3",
       "HUB_LIMIT_RATE": "5",
       "PYTHONUNBUFFERED": "1"}
errf = open(ERRPATH, "w")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=subprocess.DEVNULL, stderr=errf, env=env)


def _cleanup():
    if proc.poll() is None:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
    errf.close()
    shutil.rmtree(TMP, ignore_errors=True)


atexit.register(_cleanup)

try:
    check("hub ready", wait_ready(PORT))

    print("== setup: account + paid listing ==")
    c, a = req("POST", "/accounts/signup", {"agent": "s1-rt", "pow": solve_pow("signup")})
    CODE = (a or {}).get("account_code") or (a or {}).get("code")
    check("signup returns account_code", c in (200, 201) and CODE, "%s %s" % (c, a))
    c, l = req("POST", "/accounts/login", {"account_code": CODE, "agent": "s1-rt"})
    toks = (l or {}).get("tokens") or {}
    LTOK, BTOK = toks.get("list"), toks.get("book")
    check("login returns list+book tokens", c == 200 and LTOK and BTOK, "%s %s" % (c, l))
    H = {"X-Hub-Token": LTOK}
    HB = {"X-Hub-Token": BTOK}

    # paid bookings require a verified-human account - vouch (Tier-1 interim)
    AID = (l or {}).get("account_id")
    c, w = req("POST", "/accounts/vouch", {"account_id": AID},
               {"X-Admin-Key": "dev-admin-key-change-me"})
    check("vouch ok", c == 200 and w.get("verified_by") == "admin-vouch", "%s %s" % (c, w))

    c, ls = req("POST", "/listings", {"vertical": "events", "title": "S1 Gig",
                "date": "2026-12-01", "location": "X", "price": 10, "capacity": 50}, H)
    check("paid listing created", c == 201, "%s %s" % (c, ls))
    LID = ls["id"]

    print("== F1a: escrow_id Uint<64> bound ==")
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B1",
               "escrow_ref": {"contract": "c1", "escrow_id": 2**64 - 1, "tx": "0x1"}}, HB)
    check("F1a escrow_id 2^64-1 accepted", c == 201, "%s %s" % (c, w))
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B2",
               "escrow_ref": {"contract": "c1", "escrow_id": 2**64, "tx": "0x2"}}, HB)
    check("F1a escrow_id 2^64 rejected 400", c == 400, "%s %s" % (c, w))

    print("== F1b: (contract, escrow_id) uniqueness wall ==")
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B3",
               "escrow_ref": {"contract": "c1", "escrow_id": 7, "tx": "0x7"}}, HB)
    check("first claim of escrow 7 books", c == 201, "%s %s" % (c, w))
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B4",
               "escrow_ref": {"contract": "c1", "escrow_id": 7, "tx": "0x77"}}, HB)
    check("F1b duplicate (contract,id) -> 409", c == 409 and "already claimed" in json.dumps(w),
          "%s %s" % (c, w))
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B5",
               "escrow_ref": {"contract": "c1", "escrow_id": 8, "tx": "0x7"}}, HB)
    check("same contract, different id ok", c == 201, "%s %s" % (c, w))
    c, w = req("POST", "/book", {"listing_id": LID, "attendee": "B6",
               "escrow_ref": {"contract": "c2", "escrow_id": 7, "tx": "0x7"}}, HB)
    check("different contract, same id ok", c == 201, "%s %s" % (c, w))

    print("== F2: payout + verify limits are pre-auth ==")
    c, w = req("POST", "/accounts/verify-midnight", {"credential_id": "1", "account_code": "acct-deadbeef"})
    check("verify-midnight: unknown account 403", c == 403, "%s %s" % (c, w))
    codes = []
    for i in range(5):
        c, w = req("POST", "/accounts/payout",
                   {"account_code": "acct-wrong%02d" % i, "payout_pk": "ab" * 32})
        codes.append(c)
    check("F2 wrong codes: 3x403 then 429", codes[:3] == [403, 403, 403] and 429 in codes[3:], str(codes))
    c, w = req("POST", "/accounts/payout", {"account_code": CODE, "payout_pk": "cd" * 32})
    check("F2 even VALID code 429 after exhaustion (pre-auth proof)", c == 429, "%s %s" % (c, w))

    print("== F3: rating route backstop ==")
    codes = []
    for i in range(10):
        c, w = req("POST", "/book/bk-nope-%d/rate" % i, {"stars": 5}, HB)
        codes.append(c)
        if c == 429:
            break
    check("F3 rating flood hits 429", 429 in codes, str(codes))

finally:
    # IMPORTANT: read error BEFORE cleanup deletes TMP
    err = open(ERRPATH).read() if os.path.exists(ERRPATH) else ""
    _cleanup()
    print("== F4: dev admin key warning ==")
    check("F4 dev-admin-key stderr warning",
          "WARNING: HUB_ADMIN_KEY is the dev default" in err, err[-300:] if err else "no stderr")

fails = [r for r in RESULTS if not r[1]]
print("\nS1 red-team-3: %d/%d PASS" % (len(RESULTS) - len(fails), len(RESULTS)))
sys.exit(1 if fails else 0)
