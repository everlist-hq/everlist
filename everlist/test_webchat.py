"""C9: webchat regression suite — chat-first web UI (browser surface).

Proves the FULL stack against an isolated hub (fresh state) + webchat in-process:
  - static assets + CSP/security headers + path-traversal wall
  - signup -> one-time seed -> whoami session continuity via sid cookie
  - free-text fallback search + filtered search
  - rate limit 429, body cap 413, wrong content type 415, bad JSON 400
  - reset clears the session (whoami returns to anonymous)

Run: ./venv/bin/python test_webchat.py
"""
import json
import os
import re
import socket
import subprocess

import chatlib
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import http.cookiejar

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import webchat  # in-process server (like the hub suites import app)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


WC_PORT = free_port()
HUB_PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-webchat-e2e-")
STATE = os.path.join(TMP, "state.json")

# ---- start isolated hub ----------------------------------------------------
HUB_ENV = dict(os.environ, HUB_STATE_FILE=STATE)
hub_proc = subprocess.Popen(
    [sys.executable, "app.py", str(HUB_PORT)],
    cwd=HERE, env=HUB_ENV,
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)

# ---- start webchat (in-process, threaded) ----------------------------------
webchat.PORT = WC_PORT
webchat.HUB_URL = f"http://localhost:{HUB_PORT}"
webchat.BIND = "127.0.0.1"
srv = webchat.ThreadingHTTPServer(("127.0.0.1", WC_PORT), webchat.Handler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()

BASE = f"http://127.0.0.1:{WC_PORT}"
PASS = []
FAIL = []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(("PASS" if ok else "FAIL") + f" {name}" + (f" -- {detail}" if detail else ""))


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path, headers=None):
        req = urllib.request.Request(BASE + path, headers=headers or {})
        try:
            with self.opener.open(req, timeout=10) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def chat(self, text):
        data = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            BASE + "/api/chat", data=data,
            headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=30) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def sid(self):
        for c in self.jar:
            if c.name == "sid":
                return c.value
        return None


def wait_hub(timeout=20):
    url = f"http://localhost:{HUB_PORT}/verticals"
    for _ in range(timeout * 5):
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def main():
    ok = wait_hub()
    if not ok:
        print("FATAL: isolated hub did not come up")
        return 1

    c = Client()

    # 1. static + headers
    code, hdrs, body = c.get("/")
    check("index 200", code == 200 and b"EverList" in body)
    check("CSP present", "content-security-policy" in {k.lower() for k in hdrs})
    check("nosniff", hdrs.get("X-Content-Type-Options") == "nosniff")
    code, _, _ = c.get("/style.css")
    check("css 200", code == 200)
    code, _, _ = c.get("/app.js")
    check("js 200", code == 200)
    code, _, _ = c.get("/favicon.svg")
    check("favicon 200", code == 200)
    code, _, _ = c.get("/../app.py")
    check("traversal blocked", code in (403, 404))

    # 2. health
    code, _, body = c.get("/api/health")
    check("health ok", code == 200 and json.loads(body)["ok"] is True)

    # 3. chat: greeting
    code, _, body = c.chat("hi")
    check("greeting", code == 200 and b"EverList" in body)

    # 4. signup -> one-time seed -> session continuity
    code, _, body = c.chat("signup")
    txt = json.loads(body).get("reply", "")
    check("signup reply has seed", code == 200 and "login-seed" in txt)
    check("sid cookie set", bool(c.sid()))
    code, _, body = c.chat("whoami")
    txt = json.loads(body).get("reply", "")
    check("whoami logged in", code == 200 and "acct-" in txt)

    # 5. fallback search + filtered search
    code, _, body = c.chat("jazz")
    txt = json.loads(body).get("reply", "")
    check("fallback search", code == 200 and "listing" in txt.lower())
    code, _, body = c.chat("search pizza under 10")
    txt = json.loads(body).get("reply", "")
    check("filtered search", code == 200 and chatlib._mb("Pizza") in txt)

    # 6. reset clears session
    req = urllib.request.Request(BASE + "/api/reset", method="POST")
    with c.opener.open(req, timeout=10) as r:
        check("reset 200", r.status == 200)
    code, _, body = c.chat("whoami")
    txt = json.loads(body).get("reply", "")
    check("session cleared", code == 200 and "acct-" not in txt)

    # 6b. W1: dashboard + escrow actions (fresh clients; tokens stay server-side)
    def wpost(cl, path, payload):
        req = urllib.request.Request(BASE + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with cl.opener.open(req, timeout=10) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}

    def vouch(aid):
        req = urllib.request.Request(f"http://localhost:{HUB_PORT}/accounts/vouch",
            data=json.dumps({"account_id": aid}).encode(), method="POST",
            headers={"Content-Type": "application/json", "X-Admin-Key": "dev-admin-key-change-me"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    anon = Client()
    code, _, body = anon.get("/api/dashboard")
    d = json.loads(body)
    check("dashboard anonymous empty", code == 200 and d["account"] is None
          and d["bookings"] == [] and d["orders"] == [])

    seller = Client()
    _, _, body = seller.chat("signup")
    check("seller signup seed", bool(re.search(r"[0-9a-f]{64}", json.loads(body).get("reply", ""))))
    aid_a = json.loads(seller.get("/api/me")[2])["account"]["account_id"]
    check("seller me after signup", aid_a and aid_a.startswith("acct-"))
    check("seller vouch", vouch(aid_a) == 200)
    code, _, body = seller.chat("list Zebra Quiz Night | community | 2026-10-01 | 0 | Berlin | 5")
    check("seller listed", code == 200 and "Zebra Quiz Night" in json.loads(body).get("reply", ""))

    buyer = Client()
    buyer.chat("signup")
    aid_b = json.loads(buyer.get("/api/me")[2])["account"]["account_id"]
    check("buyer vouch", vouch(aid_b) == 200)
    code, _, body = buyer.chat("search zebra")
    check("buyer found listing", code == 200 and chatlib._mb("Zebra Quiz Night") in json.loads(body).get("reply", ""))
    code, _, body = buyer.chat("book 1 W1 Buyer")
    reply = json.loads(body).get("reply", "")
    check("buyer booked free", code == 200 and "Booked" in reply, reply[:60])

    code, _, body = buyer.get("/api/dashboard")
    d = json.loads(body)
    check("buyer dashboard has booking", code == 200 and len(d["bookings"]) == 1)
    bk = d["bookings"][0]
    check("booking projection safe", bk.get("title") == "Zebra Quiz Night"
          and "cancel_token" not in bk and "booking_secret" not in bk)
    check("booking can_cancel", bk.get("can_cancel") is True and bk.get("escrow") == "WAIVED")

    code, res = wpost(buyer, "/api/cancel", {"booking_id": bk["id"]})
    check("cancel refunds", code == 200 and res.get("escrow") == "REFUNDED")

    buyer.chat("search zebra")
    buyer.chat("book 1 W1 Buyer")
    code, _, body = buyer.get("/api/dashboard")
    bk2 = next(b for b in json.loads(body)["bookings"] if b["escrow"] == "WAIVED")
    code, _, body = seller.get("/api/dashboard")
    d = json.loads(body)
    check("seller sees order", code == 200 and any(o["id"] == bk2["id"] for o in d["orders"]))
    od = next(o for o in d["orders"] if o["escrow"] == "WAIVED")
    check("order can_confirm", od.get("can_confirm") is True)
    code, res = wpost(seller, "/api/confirm", {"booking_id": od["id"]})
    check("confirm releases", code == 200 and res.get("escrow") == "RELEASED")
    code, res = wpost(seller, "/api/confirm", {"booking_id": od["id"]})
    check("confirm single-use 409", code == 409)

    code, _ = wpost(buyer, "/api/logout", {})
    code, _, body = buyer.get("/api/dashboard")
    check("logout clears dashboard", code == 200 and json.loads(body)["account"] is None)

    # 7. protocol walls
    # 413
    check("413 body cap", _status(c.opener, urllib.request.Request(
        BASE + "/api/chat", data=json.dumps({"text": "a" * 40000}).encode(),
        headers={"Content-Type": "application/json"})) == 413)
    # 415
    check("415 wrong type", _status(c.opener, urllib.request.Request(
        BASE + "/api/chat", data=b"hi",
        headers={"Content-Type": "text/plain"})) == 415)
    # 400
    check("400 bad json", _status(c.opener, urllib.request.Request(
        BASE + "/api/chat", data=b"not json",
        headers={"Content-Type": "application/json"})) == 400)

    # 8. rate limit (session bucket: burst 8) — send rapid greetings
    codes = [ _status(c.opener, _chat_req("hi")) for _ in range(12) ]
    check("429 rate limited", 429 in codes, str(codes))

    # cleanup
    srv.shutdown()
    hub_proc.terminate()
    try:
        hub_proc.wait(timeout=5)
    except Exception:
        hub_proc.kill()

    print(f"\nwebchat suite: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", ", ".join(FAIL))
        return 1
    return 0


def _chat_req(text):
    return urllib.request.Request(
        BASE + "/api/chat", data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"})


def _status(opener, req):
    try:
        with opener.open(req, timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


if __name__ == "__main__":
    sys.exit(main())
