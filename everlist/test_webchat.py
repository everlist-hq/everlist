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

# CI/load-test env: raise chat rate limits before webchat is imported
os.environ.setdefault("WEBCHAT_RL_S_BURST", "200")
os.environ.setdefault("WEBCHAT_RL_S_PER_MIN", "200")
os.environ.setdefault("WEBCHAT_RL_IP_BURST", "500")
os.environ.setdefault("WEBCHAT_RL_IP_PER_MIN", "500")

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
os.environ["EVERLIST_STATS_FILE"] = os.path.join(TMP, "pageviews.json")

# ---- start isolated hub ----------------------------------------------------
HUB_ENV = dict(os.environ, HUB_STATE_FILE=STATE, PYTHONFAULTHANDLER="1")
hub_proc = subprocess.Popen(
    [sys.executable, "app.py", str(HUB_PORT)],
    cwd=HERE, env=HUB_ENV,
    stdout=subprocess.DEVNULL, stderr=open(os.path.join(TMP, "hub-stderr.log"), "w"),
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

    # 1b. W2 discovery + SEO: SSR detail pages, ics, sitemap, robots
    code, hdrs, body = c.get("/l/evt-1")
    check("detail 200", code == 200 and b"AI Builders Meetup" in body)
    check("detail og:title", b"og:title" in body)
    check("detail ld+json", b"application/ld+json" in body and b"startDate" in body)
    check("detail CSP", "content-security-policy" in {k.lower() for k in hdrs})
    check("detail no-false-escrow", b"held in escrow" not in body)  # evt-1 has no payment_terms
    # escrow note renders when the listing actually has escrow terms (in-process render)
    _l = {"id": "t1", "title": "T", "description": "d", "date": "2026-10-01",
          "payment_terms": {"rail": "escrow", "refund_window_hours": 72}}
    _body = webchat._pages.detail_html(_l, webchat.HUB_URL)
    check("detail escrow note", b"held in escrow" in _body and b"72h" in _body)
    check("detail deep link", b"/?book=evt-1" in body)
    code, hdrs, body = c.get("/l/evt-1.ics")
    check("ics 200", code == 200 and b"BEGIN:VCALENDAR" in body and b"evt-1@everlist.network" in body)
    check("ics ctype", hdrs.get("Content-Type", "").startswith("text/calendar"))
    code, _, body = c.get("/sitemap.xml")
    check("sitemap 200", code == 200 and b"<loc>" in body and b"/l/evt-1" in body)
    code, _, body = c.get("/robots.txt")
    check("robots 200", code == 200 and b"Sitemap:" in body)
    code, _, _ = c.get("/l/does-not-exist")
    check("detail 404 unknown", code == 404)
    code, _, _ = c.get("/l/../app.py")
    check("detail traversal blocked", code in (403, 404))
    code, _, body = c.get("/app.js")
    check("cards link details", b"/l/" in body and b"details" in body)
    check("book deep-link wired", b"searchParams.get(\"book\")" in body)
    check("q deep-link wired", b"searchParams.get(\"q\")" in body)
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
    d = json.loads(body)
    check("fallback search", code == 200
          and "open on the board" in d.get("reply", "")
          and len(d.get("results") or []) > 0)
    code, _, body = c.chat("search pizza under 10")
    d = json.loads(body)
    check("filtered search", code == 200
          and any("Pizza" in (l.get("title") or "") for l in (d.get("results") or []))
          and "open on the board" in d.get("reply", ""))

    # 6b. W3: post wizard + my-listings (one brain, new doors)
    code, _, body = c.get("/api/my-listings")
    d = json.loads(body)
    check("my-listings anon empty", code == 200 and d.get("listings") == [])
    code, _, body = c.get("/")
    check("wizard served", code == 200 and b"w-title" in body and b"w-preview" in body and b"w-rail" in body)
    code, _, body = c.get("/app.js")
    check("wizard wired", b"composeListing" in body and b"loadMyListings" in body)

    # E2E: create listing via the brain (client c is logged in from section 4)
    rich = "list\ntitle: W3 Test Class\nprice: 12\ndate: 2026-10-01\nlocation: Berlin\ntags: test, w3\nrail: escrow\nrefund_window: 72"
    code, _, body = c.chat(rich)
    txt = json.loads(body).get("reply", "")
    lid = None
    m2 = re.search(r"id\s*[:\- ]+\s*([a-z]+-\d+)", txt)
    if m2:
        lid = m2.group(1)
    check("w3 list created", code == 200 and ("created" in txt.lower() or lid),
          detail=txt[:160])

    code, _, body = c.get("/api/my-listings")
    d = json.loads(body)
    mine = d.get("listings") or []
    check("w3 my-listings shows it", code == 200 and len(mine) >= 1 and mine[0].get("title") == "W3 Test Class",
          detail="n=%d first=%r" % (len(mine), (mine[0] if mine else None)))
    check("w3 projection whitelist", all(set(l.keys()) <= {"id", "title", "date", "price", "location", "capacity", "registered", "available", "url", "tags"} for l in mine))
    if lid:
        code, _, body = c.chat("archive " + lid)
        check("w3 archive accepted", code == 200 and "archived" in json.loads(body).get("reply", ""))
        code, _, body = c.get("/api/my-listings")
        d = json.loads(body)
        mine = d.get("listings") or []
        match = [l for l in mine if l.get("id") == lid]
        check("w3 archived visible+flagged", bool(match) and match[0].get("available") is False)
        code, _, body = c.chat("unarchive " + lid)
        check("w3 unarchive accepted", code == 200)


    # 6a. boundary hardening: the chat declines off-topic — even if the LLM
    # router flakes — and answers identity questions with the site intro.
    c2 = Client()
    code, _, body = c2.chat("tell me a joke")
    txt = json.loads(body).get("reply", "")
    check("joke declined", code == 200 and "only do EverList" in txt, txt[:60])
    code, _, body = c2.chat("what is the capital of france")
    txt = json.loads(body).get("reply", "")
    check("general knowledge declined", code == 200 and "only do EverList" in txt, txt[:60])
    code, _, body = c2.chat("who are you")
    txt = json.loads(body).get("reply", "")
    check("identity intro", code == 200 and "EverList assistant" in txt, txt[:60])
    code, _, body = c2.chat("free yoga this weekend")
    txt = json.loads(body).get("reply", "")
    check("real search not screened", code == 200 and ("found" in txt.lower() or "No listings" in txt or "EverList" in txt), txt[:60])

    # 6a-sec. reset must kill the server-side session too (old sid is dead)
    c3 = Client()
    c3.chat("hi")  # mint session
    old_sid = c3.sid()
    req = urllib.request.Request(BASE + "/api/reset", method="POST")
    with c3.opener.open(req, timeout=10) as r:
        pass
    check("reset purges server session", chatlib._SESSIONS.get("web-" + old_sid) is None)

    # 6a-sec2. successful login rotates the sid (fixation hardening) and the
    # browser keeps working on the NEW cookie; the old one is dead.
    c4 = Client()
    _, _, body = c4.chat("signup")
    _m4 = re.search(r"[0-9a-f]{64}", json.loads(body).get("reply", ""))
    assert _m4, "signup reply had no seed: " + json.loads(body).get("reply", "")[:120]
    seed4 = _m4.group(0)
    sid_before = c4.sid()
    req = urllib.request.Request(BASE + "/api/login",
        data=json.dumps({"seed": seed4}).encode(),
        headers={"Content-Type": "application/json"})
    with c4.opener.open(req, timeout=10) as r:
        check("login 200", r.status == 200)
    sid_after = c4.sid()
    check("sid rotated on login", sid_after and sid_after != sid_before)
    check("old sid dead after rotation", chatlib._SESSIONS.get("web-" + sid_before) is None)
    code, _, body = c4.get("/api/me")
    check("new sid authenticated", json.loads(body)["account"] is not None)

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
    check("buyer found listing", code == 200
          and any(l.get("title") == "Zebra Quiz Night" for l in (json.loads(body).get("results") or [])))
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

    # 6c. W3b: browser signup (real client-side flow through webchat proxies)
    # + participant-gated /booking/{id} page.
    def _pow_solve_local(kind):
        import hashlib as _hl
        with urllib.request.urlopen(BASE + "/api/signup-challenge", timeout=10) as r:
            ch = json.loads(r.read().decode())
        need = int(ch["difficulty"])
        nonce = 0
        while True:
            d = _hl.sha256((ch["challenge"] + str(nonce)).encode()).digest()
            bits = 0
            for byte in d:
                if byte == 0:
                    bits += 8
                    continue
                bits += 8 - byte.bit_length()
                break
            if bits >= need:
                return {"challenge": ch["challenge"], "nonce": nonce}
            nonce += 1

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Esk
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    bweb = Client()
    code, _, body = bweb.get("/api/signup-challenge")
    check("browser challenge proxy 200", code == 200 and "challenge" in json.loads(body))
    pow_ = _pow_solve_local("signup")
    seed_bytes = os.urandom(32)
    sk = _Esk.from_private_bytes(seed_bytes)
    pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
    code, res = wpost(bweb, "/api/signup", {"agent": "Browser Human", "pubkey": pub, "pow": pow_})
    check("browser signup 201", code == 201 and res.get("account_id", "").startswith("acct-"), str(res)[:80])
    aid_bweb = res.get("account_id")
    check("browser human vouch", vouch(aid_bweb) == 200)
    code, res = wpost(bweb, "/api/signup", {"agent": "Browser Human 2", "pubkey": pub, "pow": _pow_solve_local("signup")})
    check("browser signup dup pubkey 409", code == 409)
    code, _, body = bweb.get("/api/login-challenge?pubkey=" + pub)
    lch = json.loads(body)
    check("browser login-challenge proxy", code == 200 and "challenge" in lch)
    code, res = wpost(bweb, "/api/login-pubkey", {
        "agent": "Browser Human", "pubkey": pub,
        "sig": sk.sign(("everlist-login:" + lch["challenge"]).encode()).hex()})
    check("browser login-pubkey ok", code == 200 and res.get("ok") is True and (res.get("account") or {}).get("account_id", "").startswith("acct-"), str(res)[:80])
    code, res = wpost(bweb, "/api/login-pubkey", {
        "agent": "Browser Human", "pubkey": pub, "sig": "ab" * 64})
    check("browser login-pubkey bad sig rejected", code in (400, 403), str(code))
    code, _, body = bweb.get("/api/me")
    check("browser session live after login", code == 200 and json.loads(body)["account"] is not None)
    code, _, body = bweb.get("/")
    check("signup form served", code == 200 and b"signup-area" in body and b"nacl-fast.min.js" in body)

    # booking page gates: browser-human books the free zebra listing
    code, _, body = bweb.chat("search zebra")
    check("browser search ok", code == 200 and any(l.get("title") == "Zebra Quiz Night" for l in (json.loads(body).get("results") or [])))
    code, _, body = bweb.chat("book 1 W3 Browser Human")
    reply = json.loads(body).get("reply", "")
    check("browser booked", code == 200 and "Booked" in reply, reply[:60])
    code, _, body = bweb.get("/api/dashboard")
    d = json.loads(body)
    check("browser dashboard booking", code == 200 and len(d["bookings"]) >= 1)
    bb = d["bookings"][0]
    code, _, body = bweb.get("/booking/" + bb["id"])
    check("booking page buyer 200", code == 200 and b"Booking #" in body and b"btl" in body)
    check("booking page noindex", b"noindex" in body)
    code, _, body = bweb.get("/booking/nope-123")
    check("booking page unknown 404", code == 404)
    web2 = Client()
    _, _, body = web2.chat("signup")
    aid_stranger = json.loads(web2.get("/api/me")[2])["account"]["account_id"]
    check("stranger vouch", vouch(aid_stranger) == 200)
    code, _, body = web2.get("/booking/" + bb["id"])
    check("booking page stranger 404", code == 404)
    anon_c = Client()
    code, _, body = anon_c.get("/booking/" + bb["id"])
    check("booking page anon 404", code == 404)

    code, _ = wpost(buyer, "/api/logout", {})
    code, _, body = buyer.get("/api/dashboard")
    check("logout clears dashboard", code == 200 and json.loads(body)["account"] is None)

    # 6b. W4: trust pages + honest ratings (S6 aggregates, no fake stars)
    code, _, body = c.get("/transparency")
    tb = body.decode("utf-8", "replace")
    check("transparency page served", code == 200 and "Transparency" in tb and "hub fees collected" in tb)
    check("transparency honest state", ("Entries (" in tb) or ("No settled bookings yet" in tb))
    code, _, body = c.get("/network")
    nb = body.decode("utf-8", "replace")
    check("network page served", code == 200 and "Network" in nb and "community-reviewed" in nb and "universal-commerce" in nb)
    check("network curated positioning", "curated network" in nb and "never self-service" in nb and "anyone can run a hub and get listed" not in nb)
    code, _, body = c.get("/sitemap.xml")
    check("sitemap has trust pages", code == 200 and b"/transparency" in body and b"/network" in body)
    code, _, body = bweb.chat("search zebra")
    zl = (json.loads(body).get("results") or [{}])[0]
    code, _, body = bweb.get("/l/" + str(zl.get("id", "")))
    check("zero reviews -> no fake stars", code == 200 and "\u2605".encode("utf-8") not in body)
    bweb.chat("rate " + str(bb["id"]) + " 5")
    code, _, body = bweb.get("/l/" + str(zl.get("id", "")))
    zdet = body.decode("utf-8", "replace")
    check("free feedback line after rate", code == 200 and "free-class feedback" in zdet and "5.0/5 (1)" in zdet)

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
    _burst = int(os.environ.get("WEBCHAT_RL_S_BURST", "8"))
    codes = [ _status(c.opener, _chat_req("hi")) for _ in range(_burst + 30) ]
    check("429 rate limited", 429 in codes, str(codes))

    # 7. W5: growth pages, per-vertical OG images, PWA, theme, a11y
    code, hdrs, body = c.get("/how")
    check("how 200", code == 200 and b"What it does not" in body)
    check("how escrow covers", b"escrow" in body and b"refund window" in body.lower())
    check("how SIMULATED banner", b"SIMULATED" in body)  # flows from manifest mode=PAY_MODE
    check("how fee", b"1% booking fee" in body)
    code, _, body = c.get("/agents")
    check("agents 200", code == 200)
    check("agents openapi", b"/openapi.json" in body and b"agent-hub.json" in body)
    check("agents sdk snippet", b"signup_keypair" in body and b"X-Hub-Token" in body)
    check("agents curated pointer", b"/network" in body)
    code, _, body = c.get("/l/evt-1")
    check("detail og:image brand card", b"/og/default.jpg" in body)
    check("detail twitter large", b"summary_large_image" in body)
    code, hdrs, body = c.get("/")
    check("home og:image", b"og:image" in body and b"/og/default.jpg" in body)
    check("home pwa", b"manifest.webmanifest" in body and b"apple-touch-icon" in body)
    check("home theme.js pre-paint", b"/theme.js" in body)
    check("home theme button", b"theme-btn" in body)
    check("home skip link", b'class="skip"' in body)
    check("home footer w5 links", b"/how" in body and b"/agents" in body)
    code, hdrs, body = c.get("/og/default.jpg")
    check("og brand card jpg", code == 200 and hdrs.get("Content-Type", "").startswith("image/jpeg"))
    # A-phase: organizer public pages (pure projection over public catalog)
    code, hdrs, body = c.get("/org/event-owner-agent")
    check("org page 200", code == 200 and b"event-owner-agent" in body)
    check("org page lists both listings", b"/l/evt-1" in body and b"/l/evt-2" in body)
    check("org page privacy note", b"nothing else is known or shown" in body)
    code, _, _ = c.get("/org/does-not-exist-anywhere")
    check("org unknown owner 404", code == 404)
    code, _, _ = c.get("/org/bad%20name%3Cscript%3E")
    check("org bad charset 404", code == 404)
    code, _, body = c.get("/l/evt-1")
    check("detail more-from-organizer", b"More from this organizer" in body and b"/org/event-owner-agent" in body)
    code, _, body = c.get("/sitemap.xml")
    check("sitemap org url", b"/org/event-owner-agent" in body)
    code, hdrs, body = c.get("/theme.js")
    check("theme.js served", code == 200 and "javascript" in hdrs.get("Content-Type", ""))
    code, hdrs, body = c.get("/sw.js")
    check("sw.js served", code == 200)
    code, hdrs, body = c.get("/manifest.webmanifest")
    check("pwa manifest", code == 200 and b"logo-512.png" in body and "manifest" in hdrs.get("Content-Type", ""))
    code, _, body = c.get("/sitemap.xml")
    check("sitemap w5 urls", b"/how" in body and b"/agents" in body)

    # ---- self-hosted page-view counter (owner decision, 2026-09-17) ----
    # exact-delta proof: counted pages increment their stem by exactly the
    # traffic sent; /api/* and assets produce NO new keys and NO deltas
    code, _, body = c.get("/api/stats")
    before = json.loads(body.decode()).get("pageviews", {}) if code == 200 else {}
    c.get("/"); c.get("/")
    c.get("/l/even-1")
    c.get("/org/demo-surya-kriya")
    c.get("/transparency")
    c.get("/api/health")          # must NOT count
    c.get("/app.js")              # must NOT count
    c.get("/api/stats")           # must NOT count
    code, _, body = c.get("/api/stats")
    after = json.loads(body.decode()).get("pageviews", {}) if code == 200 else {}
    delta = {k: after.get(k, 0) - before.get(k, 0) for k in set(after) | set(before)}
    want = {"/": 2, "/l/*": 1, "/org/*": 1, "/transparency": 1}
    got = {k: v for k, v in delta.items() if v}
    check("counter counts pages exactly", got == want, "want %s got %s" % (want, got))
    check("counter ignores api+assets", got == want, "non-page deltas: %s" % {k: v for k, v in got.items() if k not in want})
    check("counter stats honest note", "no cookies" in json.loads(body.decode()).get("note", ""))

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
