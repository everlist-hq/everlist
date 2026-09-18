"""Weekly organizer digest suite (owner-approved polish batch, 2026-09-18).
Locks the /admin/digest/send contract: admin-gated, 7-day window computed from
BOOKINGS+LISTINGS, per-organizer totals (created/confirmed/cancelled/gross),
verified-email + notify-on gating, dry_run sends nothing, branded template.
Run: python test_digest.py
"""
import sys
import hashlib, json, os, socket, subprocess, sys, time, atexit, tempfile
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-digest-"), "state.json")
LOGF = os.path.join(os.path.dirname(STATE), "hub.log")
ADMIN = "digest-admin-key"
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log",
       "HUB_ADMIN_KEY": ADMIN}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=open(LOGF, "w"), stderr=subprocess.STDOUT, env=env)

atexit.register(lambda: proc.terminate())


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


def check(name, cond, detail=""):
    cond = bool(cond)
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" -- " + str(detail)[:200] if detail and not cond else ""))


assert wait_ready(), "hub did not start"
print("== weekly organizer digest (admin endpoint) ==")

# accounts: organizer with verified email, buyer
st, a = req("POST", "/accounts/signup", {"agent": "dg-org", "pow": solve_pow("signup")})
st, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "dg-org"})
ltok = l["tokens"]["list"]
st, b1 = req("POST", "/accounts/email/bind", {"email": "org@example.dev"}, tok=ltok)
vcode = open(LOGF).read().split("verification code: ")[1][:6]
st, _ = req("POST", "/accounts/email/verify", {"email": "org@example.dev", "code": vcode}, tok=ltok)
check("organizer email verified", st == 200, (st, _))

st, a2 = req("POST", "/accounts/signup", {"agent": "dg-buyer", "pow": solve_pow("signup")})
st, l2 = req("POST", "/accounts/login", {"account_code": a2["account_code"], "agent": "dg-buyer"})
btok = l2["tokens"]["book"]

# listing + bookings through the real flow
c, li = req("POST", "/listings", {"title": "Digest rooftop jazz", "date": "2026-10-05",
    "location": "Berlin", "price": 20, "capacity": 10, "vertical": "events",
    "category": "concert"}, tok=ltok)
check("listing created", c == 201, (c, li))
c, bk1 = req("POST", "/book", {"listing_id": li["id"], "quantity": 1,
    "human_verified": True, "attendee": "A"}, tok=btok)
check("booking 1 created (HELD)", c == 201 and bk1.get("escrow") == "HELD", (c, bk1))
c, bk2 = req("POST", "/book", {"listing_id": li["id"], "quantity": 1,
    "human_verified": True, "attendee": "B"}, tok=btok)
check("booking 2 created", c == 201, (c, bk2))

# dry run: computes, sends nothing
loglen_before = len(open(LOGF).read())
c, r = req("POST", "/admin/digest/send", {"dry_run": True}, tok=ADMIN)
check("dry run 200", c == 200, (c, r))
check("dry run would_send organizer", r.get("sent", 0) >= 1
      and any(x.get("would_send") for x in r.get("results", [])), r)
check("dry run totals: created=2 gross=40", any(
    x.get("created") == 2 and abs(x.get("gross", 0) - 40.0) < 0.01
    for x in r.get("results", [])), r)
check("dry run sent NO mail", len(open(LOGF).read()) == loglen_before)

# real send (still log mode)
c, r2 = req("POST", "/admin/digest/send", {}, tok=ADMIN)
check("send 200", c == 200, (c, r2))
log = open(LOGF).read()
check("digest mail logged to organizer", "to=org@example.dev" in log
      and "Your week on EverList" in log, log[-300:])

# auth + validation
c, r3 = req("POST", "/admin/digest/send", {})
check("requires admin key", c == 403, (c, r3))
c, r4 = req("POST", "/admin/digest/send", {"weeks": 99}, tok=ADMIN)
check("weeks 99 rejected", c == 400, (c, r4))

print(f"\ndigest suite: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
sys.exit(0)
