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
import socket
import subprocess
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
    check("filtered search", code == 200 and "Pizza" in txt)

    # 6. reset clears session
    req = urllib.request.Request(BASE + "/api/reset", method="POST")
    with c.opener.open(req, timeout=10) as r:
        check("reset 200", r.status == 200)
    code, _, body = c.chat("whoami")
    txt = json.loads(body).get("reply", "")
    check("session cleared", code == 200 and "acct-" not in txt)

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
