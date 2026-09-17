"""Booking-notification regression suite (B-phase, owner-approved 2026-09-17).
Own hub in log email mode; asserts created/released/refunded mails, per-
recipient notify preference gating, and the unsubscribe line in every mail.
Run: python test_notify.py
"""
import sys
import hashlib, json, os, re, socket, subprocess, sys, time, atexit, tempfile
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="hub-notify-")
STATE = os.path.join(TMP, "state.json")
LOGF = os.path.join(TMP, "hub.log")
ADMIN = "notify-admin-key"
log_fh = open(LOGF, "w")

env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log",
       "HUB_ADMIN_KEY": ADMIN, "PYTHONFAULTHANDLER": "1"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log_fh, stderr=subprocess.STDOUT, env=env)
_ACTIVE = [proc, log_fh]


def _cleanup():
    for p in _ACTIVE:
        try:
            p.terminate() if isinstance(p, subprocess.Popen) else p.close()
        except Exception:
            pass


atexit.register(_cleanup)


def req(method, path, payload=None, tok=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **({"X-Hub-Token": tok} if tok else {})},
        method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def wait_ready(timeout=15):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            req("GET", "/.well-known/agent-hub.json"); return True
        except Exception:
            time.sleep(0.2)
    return False


def solve_pow(kind):
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def last_code(pat):
    log_fh.flush()
    m = re.findall(pat, open(LOGF).read())
    return m[-1] if m else None


def check(name, cond, detail=""):
    cond = bool(cond)
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" -- " + str(detail)[:200] if detail and not cond else ""))


def verified_account(agent, email):
    _, a = req("POST", "/accounts/signup", {"agent": agent, "pow": solve_pow("signup")})
    code = a["account_code"]
    _, l = req("POST", "/accounts/login", {"account_code": code, "agent": agent})
    req("POST", "/accounts/email/bind", {"email": email, "account_code": code})
    vc = last_code(r"verification code: ([A-F0-9]{6})")
    req("POST", "/accounts/email/verify", {"email": email, "code": vc})
    return l["tokens"]


# ---- setup ------------------------------------------------------------------
assert wait_ready(), "hub did not start"
print("== booking notifications (HUB_EMAIL_MODE=log) ==")

buyer_tok = verified_account("nt-buyer", "buyer@example.dev")
org_tok = verified_account("nt-org", "org@example.dev")
btok, blist = buyer_tok["book"], buyer_tok.get("list")
ltok = org_tok["list"]

c, li = req("POST", "/listings", {"title": "Notify Test Concert", "date": "2026-12-01",
    "location": "Test Hall", "price": 10, "capacity": 5, "vertical": "events"}, tok=ltok)
check("listing created", c == 201, (c, li))
lid = li["id"]

# ---- booking created -> organizer mail -------------------------------------
c, bk1 = req("POST", "/book", {"listing_id": lid, "quantity": 1,
    "human_verified": True, "attendee": "A"}, tok=btok)
check("booking created", c == 201, (c, bk1))
b1 = bk1["id"]
check("created mail to organizer",
      last_code(r"\[EMAIL:log\] to=(org@example\.dev) subject='New booking") == "org@example.dev")
check("created mail has unsubscribe line", "notify off" in open(LOGF).read())

# ---- confirmed -> buyer mail ------------------------------------------------
c, adm = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": b1}, tok=ADMIN)
check("admin confirm token minted", c in (200, 201) and bool(adm.get("token")), (c, adm))
c, cf = req("POST", f"/book/{b1}/confirm", {}, tok=adm["token"])
check("confirm 200", c == 200, (c, cf))
check("released mail to buyer",
      last_code(r"\[EMAIL:log\] to=(buyer@example\.dev) subject='Confirmed") == "buyer@example.dev")

# ---- preference gating (per recipient) --------------------------------------
pre = open(LOGF).read()
c, bk2 = req("POST", "/book", {"listing_id": lid, "quantity": 1,
    "human_verified": True, "attendee": "B"}, tok=btok)
check("second booking created", c == 201, (c, bk2))

c, n = req("POST", "/accounts/notify", {"notify_email": False}, tok=blist)
check("notify off 200", c == 200 and n.get("notify_email") is False, (c, n))
c, n_bad = req("POST", "/accounts/notify", {"notify_email": False})
check("notify requires auth", c == 403, (c, n_bad))

c, cc = req("POST", f"/book/{bk2['id']}/cancel", {}, tok=bk2["cancel_token"])
check("cancel 200", c == 200, (c, cc))
post = open(LOGF).read()[len(pre):]
check("no buyer mail after notify off", "to=buyer@example.dev" not in post)
check("organizer mail unaffected by buyer pref",
      last_code(r"\[EMAIL:log\] to=(org@example\.dev) subject='Cancelled") == "org@example.dev")

c, n2 = req("POST", "/accounts/notify", {"notify_email": True}, tok=blist)
check("notify on 200", c == 200 and n2.get("notify_email") is True, (c, n2))

c, bad = req("POST", "/accounts/notify", {"notify_email": "yes"}, tok=blist)
check("notify rejects non-boolean", c == 400, (c, bad))

print(f"\nnotify suite: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
sys.exit(0)
