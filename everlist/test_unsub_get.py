"""S1 red-team regression + owner email-split decision (2026-09-18):
GET /accounts/notify/unsubscribe used to crash the handler (module-level
_html_resp called as self._html_resp) -> empty reply at the edge / 502 in
production. Locks: garbage token -> 200 branded HTML page (never a crash);
valid token -> 200 + MARKETING_EMAIL flipped off while NOTIFY_EMAIL is
preserved (transactional booking mails keep sending; codes are never gated);
idempotent second hit; POST RFC8058 garbage -> 400 JSON.
Run: python test_unsub_get.py
"""
import hashlib, json, os, socket, subprocess, sys, time, atexit, tempfile, hmac
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-unsub-"), "state.json")
LOGF = os.path.join(os.path.dirname(STATE), "hub.log")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=open(LOGF, "w"), stderr=subprocess.STDOUT, env=env)
atexit.register(lambda: proc.terminate())


def req(method, path, payload=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json"} if payload is not None else {},
        method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            ct = resp.headers.get("Content-Type", "")
            body = resp.read().decode()
            return resp.status, body if "html" in ct else json.loads(body)
    except urllib.error.HTTPError as e:
        ct = e.headers.get("Content-Type", "")
        raw = e.read().decode()
        return e.code, raw if "html" in ct else json.loads(raw or "{}")


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
            bits += bin(b).count("1") if False else (8 - b.bit_length())
            break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}: {name} {detail}")


def main():
    assert wait_ready(), "hub did not start; see " + LOGF

    # fresh account (PoW-gated signup)
    pow_sol = solve_pow("signup")
    st, a = req("POST", "/accounts/signup", {"agent": "unsub-test", "pow": pow_sol})
    check("signup 201", st == 201, str(st))
    princ = a.get("account_id") or a.get("account") or a.get("principal")
    check("signup returns principal", bool(princ), repr(a)[:120])

    # 1. garbage token -> 200 branded landing page, NEVER a crash/empty reply
    st, body = req("GET", "/accounts/notify/unsubscribe?u=acct-fake&t=deadbeef")
    check("garbage token -> 200 html", st == 200 and "<html" in body.lower(), f"st={st}")

    # 2. valid token -> 200 success page + MARKETING flag off, notify_email kept
    tok = hmac.new(b"dev-booking-key-change-me", princ.encode(), "sha256").hexdigest()[:32]
    st, body = req("GET", f"/accounts/notify/unsubscribe?u={princ}&t={tok}")
    check("valid token -> 200", st == 200, f"st={st}")
    acct = json.load(open(STATE))["accounts"][princ]
    check("marketing_email flipped False", acct.get("marketing_email") is False, repr(acct.get("marketing_email")))
    check("notify_email PRESERVED (transactional)", acct.get("notify_email") is True, repr(acct.get("notify_email")))

    # 3. idempotent second hit, notify_email still untouched
    st, body = req("GET", f"/accounts/notify/unsubscribe?u={princ}&t={tok}")
    check("second hit still 200", st == 200, f"st={st}")
    acct = json.load(open(STATE))["accounts"][princ]
    check("notify_email still True after 2nd hit", acct.get("notify_email") is True, repr(acct.get("notify_email")))

    # 4. POST RFC8058 garbage -> 400 JSON (unchanged contract)
    rq = urllib.request.Request(BASE + "/accounts/notify/unsubscribe?u=acct-x&t=yy", data=b"", method="POST")
    try:
        urllib.request.urlopen(rq, timeout=10)
        check("POST garbage 400", False, "no error raised")
    except urllib.error.HTTPError as e:
        check("POST garbage 400", e.code == 400, str(e.code))

    print(f"\nunsub-get suite: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILURES:", FAIL); sys.exit(1)


if __name__ == "__main__":
    main()
