"""H10: booking status lookup — GET /bookings/{id}.
Proves: buyer polls own booking E2E (incl. FRESH re-minted token — deterministic
principal), owner view, stranger/unknown -> indistinguishable 404 (no existence
oracle), anon 401, SDK get_booking (+HubError 404), chat 'booking <id>' for
anonymous and account senders.
Run: python test_h10_booking_status.py
"""
import hashlib, json, os, socket, subprocess, sys, tempfile, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sdk"))
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix="hub-h10-")
PORT = free_port(); BASE = f"http://127.0.0.1:{PORT}"; PYEXE = sys.executable

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
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
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

try:
    print("== H10 setup ==")
    c, a = req("POST", "/accounts/signup", {"agent": "h10-owner", "pow": solve_pow("signup")})
    assert c == 201, a
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "h10-owner"})
    assert c == 200, l
    OTOK = l["tokens"]["list"]
    c, r = req("POST", "/listings", {"vertical": "events", "title": "H10 Event",
               "date": "2026-10-10", "location": "X", "price": 5, "capacity": 10},
               {"X-Hub-Token": OTOK})
    assert c == 201, r
    LID = r["id"]
    # buyer = anonymous bootstrap agent (deterministic principal = agent name)
    BUYER = "agent1q h10-buyer".replace(" ", "")
    c, bt = req("POST", "/access", {"agent": BUYER, "acts": ["book"]})
    assert c == 201, bt
    BTOK = bt["tokens"]["book"]
    c, b = req("POST", "/book", {"listing_id": LID, "attendee": "H10 Buyer", "human_verified": True},
               {"X-Hub-Token": BTOK})
    assert c == 201, b
    BID = b["id"]

    print("== H10: buyer polls own booking ==")
    c, one = req("GET", f"/bookings/{BID}", headers={"X-Hub-Token": BTOK})
    check("buyer poll 200", c == 200, f"{c} {one}")
    check("status fields visible (escrow/amount/quantity)",
          one.get("escrow") == "HELD" and one.get("amount") == 5.0 and one.get("quantity") == 1, str(one)[:140])
    check("buyer view flag", one.get("view") == "buyer")
    c, bt2 = req("POST", "/access", {"agent": BUYER, "acts": ["book"]})
    assert c == 201, bt2
    c, one2 = req("GET", f"/bookings/{BID}", headers={"X-Hub-Token": bt2["tokens"]["book"]})
    check("FRESH re-minted token still polls (deterministic principal)", c == 200, f"{c}")

    print("== H10: owner view + access walls ==")
    c, one3 = req("GET", f"/bookings/{BID}", headers={"X-Hub-Token": OTOK})
    check("listing-owner poll 200 (view=owner)", c == 200 and one3.get("view") == "owner", f"{c} {one3}")
    c, st = req("POST", "/access", {"agent": "h10-stranger", "acts": ["book"]})
    assert c == 201, st
    c, s404 = req("GET", f"/bookings/{BID}", headers={"X-Hub-Token": st["tokens"]["book"]})
    check("stranger poll -> 404", c == 404, f"{c}")
    c, u404 = req("GET", "/bookings/bk-doesnotexist", headers={"X-Hub-Token": st["tokens"]["book"]})
    check("unknown poll -> 404", c == 404, f"{c}")
    check("stranger and unknown are indistinguishable (no existence oracle)",
          s404.get("error", "").startswith("no booking") and u404.get("error", "").startswith("no booking"))
    check("anon poll -> 401", req("GET", f"/bookings/{BID}")[0] == 401)

    print("== H10: SDK get_booking ==")
    from agenthub import AgentHub, HubError
    cl = AgentHub(BASE)
    cl.bootstrap("h10-sdk-buyer", acts=("book",))
    c, sb = req("POST", "/book", {"listing_id": LID, "attendee": "SDK Buyer", "human_verified": True},
                {"X-Hub-Token": cl._token("book")})
    assert c == 201, sb
    st_res = cl.get_booking(sb["id"])
    check("SDK get_booking returns status", st_res.get("escrow") == "HELD" and st_res.get("view") == "buyer", str(st_res)[:120])
    try:
        cl.get_booking("bk-nope")
        check("SDK unknown -> HubError 404", False)
    except HubError as e:
        check("SDK unknown -> HubError 404", getattr(e, "code", getattr(e, "status", 0)) == 404)

    print("== H10: chat 'booking <id>' ==")
    import chatlib
    sender = "agent1qf h10-chat-buyer".replace(" ", "")
    c, cb = req("POST", "/access", {"agent": sender, "acts": ["book"]})
    assert c == 201, cb
    c, cbk = req("POST", "/book", {"listing_id": LID, "attendee": "Chat Buyer", "human_verified": True},
                 {"X-Hub-Token": cb["tokens"]["book"]})
    assert c == 201, cbk
    out = chatlib.handle_text(BASE, f"booking {cbk['id']}", sender=sender)
    check("chat anonymous poll (same agent identity)", cbk["id"] in out and "HELD" in out, out[:160])
    out2 = chatlib.handle_text(BASE, f"booking {cbk['id']}", sender="agent1q h10-stranger-chat".replace(" ", ""))
    check("chat stranger poll honest 404", "not visible" in out2 or "doesn't exist" in out2, out2[:140])
    # account-based poll: signup via chat, book with account token, poll via session
    acct_sender = "agent1qf h10-acct".replace(" ", "")
    r1 = chatlib.handle_text(BASE, "signup", sender=acct_sender)
    check("chat signup for account poll", "acct-" in r1, r1[:100])
    atok = chatlib._SESSIONS[acct_sender]["tokens"]["book"]
    c, abk = req("POST", "/book", {"listing_id": LID, "attendee": "Acct Buyer", "human_verified": True},
                 {"X-Hub-Token": atok})
    assert c == 201, abk
    out3 = chatlib.handle_text(BASE, f"booking {abk['id']}", sender=acct_sender)
    check("chat account poll via session token", abk["id"] in out3 and "HELD" in out3, out3[:160])
finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== h10-booking-status: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails); sys.exit(1)
print("H10_BOOKING_STATUS_ALL_PASSED")
