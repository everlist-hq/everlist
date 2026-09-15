"""EverList webchat — chat-first web UI for the hub (stdlib only, MIT).

The THIRD surface for the same chat brain (chatlib.handle_text):
  1. Agentverse wrapper (wrapper.py, uagents)
  2. CLI/test client
  3. THIS: browser chat (serves static/ + POST /api/chat)

Design (established paths — no new protocol surface):
- hub stays agent-only HTTP+JSON (SPEC 1); webchat is a separate small service
- one brain: every message goes through chatlib.handle_text(HUB_URL, ...) unchanged
- session = server-minted 192-bit random cookie (HttpOnly, SameSite=Lax; Secure
  on public hosts). The cookie value doubles as the chatlib sender key, so
  sessions cannot be forged client-side and no extra session store exists.
- chatlib._SESSIONS is a plain dict (single-threaded uagents wrapper); this
  server is threaded, so brain calls are serialized behind one lock.
  Pilot-grade choice: simple and safe; revisit if concurrent traffic matters.
- binds 127.0.0.1 by default (WEBCHAT_BIND to override): on the VPS Caddy
  reverse-proxies chat.<domain> to it; the port is never directly exposed.

Run:  venv/bin/python webchat.py [port]
Env:  WEBCHAT_PORT (default 8804), WEBCHAT_HUB_URL (default http://localhost:8802),
      WEBCHAT_BIND (default 127.0.0.1)
"""
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

PORT = int(os.environ.get("WEBCHAT_PORT", "8804"))
BIND = os.environ.get("WEBCHAT_BIND", "127.0.0.1")
HUB_URL = os.environ.get("WEBCHAT_HUB_URL", "http://localhost:8802")
# W1: confirm-token minting follows the hub's documented owner path
# (POST /admin/tokens act=confirm). Operator sets WEBCHAT_ADMIN_KEY = HUB_ADMIN_KEY.
ADMIN_KEY = os.environ.get("WEBCHAT_ADMIN_KEY", "dev-admin-key-change-me")
STATIC_DIR = os.path.join(HERE, "static")
import pages as _pages  # W2: SSR detail pages, sitemap, robots, ics
BODY_CAP = 32 * 1024          # bytes; matches hub 413 discipline
TEXT_CAP = 8000               # chars per message (UI maxlength mirrors this)
COOKIE = "sid"
SESSION_MAX_AGE = 24 * 3600   # aligned with chatlib session TTL
SID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")

# chatlib brain serialization (see docstring)
BRAIN_LOCK = threading.Lock()

# ---- W1: account-aware dashboard -----------------------------------------
# Session discipline: the sid cookie doubles as the chatlib sender, so hub
# login tokens live ONLY server-side (chatlib._SESSIONS) — the browser never
# sees one. Escrow transitions go through the hub's existing single-use
# token endpoints (/book/{id}/cancel, /book/{id}/confirm); no new money paths.
BID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# Whitelist projection: hub GET /bookings returns raw booking records;
# the dashboard only needs these fields (no secrets exist in them).
BOOKING_FIELDS = ("id", "listing_id", "vertical", "escrow", "amount", "hub_fee",
                  "owner_payout", "quantity", "created", "escrow_ref", "payment_terms")
ORDER_FIELDS = ("id", "listing_id", "vertical", "escrow", "amount", "hub_fee",
                "owner_payout", "quantity", "created", "booked_by", "escrow_ref")
_TITLES = {"ts": 0.0, "map": {}}
_TITLES_LOCK = threading.Lock()


def hub_fetch(path, token=None, payload=None):
    """Tiny hub JSON client for W1 proxy routes. Returns (status, dict).
    status 0 means the hub was unreachable."""
    url = HUB_URL.rstrip("/") + path
    if payload is None:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        if token:
            req.add_header("X-Hub-Token", token)
        try:
            with urllib.request.urlopen(req, timeout=8) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read().decode())
            except Exception:
                return e.code, {}
        except Exception:
            return 0, {}
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Hub-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def listing_titles():
    """id -> title map (30s cache) so the dashboard shows names, not bare ids."""
    now = time.time()
    with _TITLES_LOCK:
        if _TITLES["map"] and now - _TITLES["ts"] < 30:
            return _TITLES["map"]
    st, data = hub_fetch("/listings?limit=200")
    m = {}
    if st == 200:
        m = {l.get("id"): (l.get("title") or l.get("id"))
             for l in (data.get("listings") or []) if l.get("id")}
    with _TITLES_LOCK:
        if m:
            _TITLES["map"] = m
            _TITLES["ts"] = now
        return _TITLES["map"]

# ---- token-bucket rate limiting (per sender + per IP) --------------------
RL_LOCK = threading.Lock()
_RL = {}                       # key -> (tokens, last_ts)
_RL_MAX_KEYS = 10_000          # bound memory vs unique-sender spam


def allow(key, burst, per_minute):
    """Token bucket. Returns (allowed, retry_after_seconds)."""
    now = time.time()
    rate = per_minute / 60.0
    with RL_LOCK:
        if len(_RL) >= _RL_MAX_KEYS and key not in _RL:
            for k in sorted(_RL, key=lambda k: _RL[k][1])[: _RL_MAX_KEYS // 10]:
                _RL.pop(k, None)
        toks, last = _RL.get(key, (float(burst), now))
        toks = min(float(burst), toks + (now - last) * rate)
        if toks < 1.0:
            retry = max(1, int(round((1.0 - toks) / rate)))
            _RL[key] = (toks, now)
            return False, retry
        _RL[key] = (toks - 1.0, now)
        return True, 0


# ---- hub health probe (cached, cheap public endpoint) --------------------
_HUB_STATE = {"up": None, "ts": 0.0}
_HUB_LOCK = threading.Lock()


def hub_ok():
    now = time.time()
    if _HUB_STATE["up"] is not None and now - _HUB_STATE["ts"] < 5:
        return _HUB_STATE["up"]
    with _HUB_LOCK:
        now = time.time()
        if _HUB_STATE["up"] is not None and now - _HUB_STATE["ts"] < 5:
            return _HUB_STATE["up"]
        try:
            with urllib.request.urlopen(HUB_URL + "/verticals", timeout=2) as r:
                up = r.status == 200
        except Exception:
            up = False
        _HUB_STATE["up"] = up
        _HUB_STATE["ts"] = now
        return up


# ---- static files ---------------------------------------------------------
TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def static_bytes(name):
    root = os.path.realpath(STATIC_DIR)
    path = os.path.realpath(os.path.join(root, name))
    if not path.startswith(root + os.sep) or not os.path.isfile(path):
        return None, None
    with open(path, "rb") as f:
        data = f.read()
    return data, TYPES.get(os.path.splitext(path)[1], "application/octet-stream")


def is_private_host(host):
    """No Secure cookie on localhost/LAN testing; Secure on public hosts."""
    h = (host or "").split(":")[0].lower()
    if h in ("localhost", "") or h.startswith("127."):
        return True
    if h.startswith("10.") or h.startswith("192.168."):
        return True
    if h.startswith("172."):
        try:
            second = int(h.split(".")[1])
            return 16 <= second <= 31
        except (ValueError, IndexError):
            return False
    return False


class Handler(BaseHTTPRequestHandler):
    server_version = "EverListWebchat/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        # request-line logging only: paths, never bodies or query content
        sys.stderr.write("[webchat] %s\n" % (fmt % args))

    # ---- helpers ----
    def common_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")

    def _send_bytes(self, code, body, ctype, cache="max-age=300"):
        """W2: send pre-rendered bytes (SSR pages, ics, xml) with CSP on HTML."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.common_headers()
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def reply(self, code, obj, cookie=None, extra=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.common_headers()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def cookie_attr(self, max_age):
        host = self.headers.get("Host", "")
        secure = "" if is_private_host(host) else "; Secure"
        return (
            COOKIE + "=%s; Path=/; Max-Age=" + str(max_age)
            + "; HttpOnly; SameSite=Lax" + secure
        )

    def set_sid(self, sid):
        return self.cookie_attr(SESSION_MAX_AGE) % sid

    def sid(self):
        """Existing valid sid, or a freshly minted one (caller sets the cookie)."""
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            pass
        morsel = c.get(COOKIE)
        if morsel and SID_RE.fullmatch(morsel.value):
            return morsel.value
        return secrets.token_urlsafe(24)

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            body, ctype = static_bytes("index.html")
        elif path.startswith("/api/"):
            return self.api_get(path)
        elif path == "/robots.txt":
            return self._send_bytes(200, _pages.ROBOTS, "text/plain; charset=utf-8", cache="max-age=3600")
        elif path == "/sitemap.xml":
            return self._send_bytes(200, _pages.sitemap_xml(HUB_URL), "application/xml; charset=utf-8", cache="max-age=3600")
        elif path.startswith("/l/") and path.endswith(".ics"):
            lid = path[3:-4]
            l = _pages.listing(lid, HUB_URL) if lid else None
            if not l:
                return self.reply(404, {"error": "not found"})
            ics = _pages.ics_body(l)
            if not ics:
                return self.reply(404, {"error": "no date"})
            return self._send_bytes(200, ics, "text/calendar; charset=utf-8")
        elif path.startswith("/l/"):
            lid = path[3:]
            l = _pages.listing(lid, HUB_URL) if lid else None
            if not l:
                return self.reply(404, {"error": "not found"})
            return self._send_bytes(200, _pages.detail_html(l, HUB_URL), "text/html; charset=utf-8", cache="max-age=3600")
        else:
            body, ctype = static_bytes(path.lstrip("/"))
        if body is None:
            if path.startswith("/api/"):
                return self.reply(404, {"error": "not found"})
            # Human-facing miss: branded 404 page (W0); API stays JSON.
            body, ctype = static_bytes("404.html")
            if body is None:
                return self.reply(404, {"error": "not found"})
            self.send_response(404)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.common_headers()
            self.send_header("Content-Security-Policy", CSP)
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # HTML must revalidate (dock/session state); other assets get a short
        # cache so deploys land within minutes (no content hashing yet, W0).
        self.send_header("Cache-Control", "no-cache" if ctype.startswith("text/html") else "max-age=300")
        self.common_headers()
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path):
        if path == "/api/health":
            try:
                import nlu as _nlu
                nlu_status = _nlu.status()
            except Exception:
                nlu_status = {"configured": False, "error": "nlu unavailable"}
            return self.reply(200, {"ok": hub_ok(), "hub": HUB_URL, "nlu": nlu_status})
        if path == "/api/listings":
            # Read-only passthrough so the browser can render the Discover grid
            # without a cross-origin call to the hub (CSP connect-src 'self').
            # Proxies hub GET /listings verbatim; touches no escrow/liveness path.
            q = self.path.split("?", 1)[1] if "?" in self.path else ""
            url = HUB_URL + "/listings" + (("?" + q) if q else "")
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=8) as r:
                    data = r.read()
            except Exception:
                return self.reply(502, {"error": "hub unreachable"})
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.common_headers()
            self.end_headers()
            self.wfile.write(data)
            return
        if path == "/api/me":
            sid = self.sid()
            sess = chatlib._session("web-" + sid)
            acct = None
            if sess:
                acct = {"account_id": sess.get("account_id"),
                        "verified": bool(sess.get("verified")),
                        "has_payout": bool(sess.get("payout_pk"))}
            return self.reply(200, {"ok": True, "account": acct},
                              cookie=self.set_sid(sid), extra={"Cache-Control": "no-store"})
        if path == "/api/dashboard":
            return self.api_dashboard()
        return self.reply(404, {"error": "not found"})

    def api_dashboard(self):
        """W1: bookings + merchant orders for this session, projected through
        a whitelist (hub records carry internal fields the UI never needs).
        Tokens stay server-side; the browser gets exactly the dashboard view."""
        sid = self.sid()
        sess = chatlib._session("web-" + sid)
        if not sess:
            return self.reply(200, {"ok": True, "account": None, "bookings": [], "orders": []},
                              cookie=self.set_sid(sid), extra={"Cache-Control": "no-store"})
        titles = listing_titles()
        book_tok = (sess.get("tokens") or {}).get("book")
        list_tok = (sess.get("tokens") or {}).get("list")
        cancels = sess.get("cancel_tokens") or {}
        bookings, orders, expired = [], [], False
        if book_tok:
            st, res = hub_fetch("/bookings", token=book_tok)
            if st == 200:
                for b in (res.get("bookings") or []):
                    proj = {k: b.get(k) for k in BOOKING_FIELDS if k in b}
                    proj["title"] = titles.get(b.get("listing_id"), b.get("listing_id"))
                    proj["can_cancel"] = b.get("escrow") in ("HELD", "WAIVED") and b.get("id") in cancels
                    bookings.append(proj)
            elif st == 401:
                expired = True
        if list_tok and not expired:
            st, res = hub_fetch("/orders", token=list_tok)
            if st == 200:
                for o in (res.get("orders") or []):
                    proj = {k: o.get(k) for k in ORDER_FIELDS if k in o}
                    proj["title"] = titles.get(o.get("listing_id"), o.get("listing_id"))
                    proj["can_confirm"] = o.get("escrow") in ("HELD", "WAIVED")
                    orders.append(proj)
            elif st == 401:
                expired = True
        acct = {"account_id": sess.get("account_id"),
                "verified": bool(sess.get("verified")),
                "has_payout": bool(sess.get("payout_pk"))}
        return self.reply(200, {"ok": True, "account": acct, "expired": expired,
                                "bookings": bookings, "orders": orders},
                          cookie=self.set_sid(sid), extra={"Cache-Control": "no-store"})

    # ---- POST ----
    def json_body(self):
        """Parse a bounded application/json body. Returns (data, err_response).
        On error err_response is a reply() result and data is None."""
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > BODY_CAP:
            return None, self.reply(413, {"error": "body too large"})
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/json":
            return None, self.reply(415, {"error": "expected application/json"})
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None, self.reply(400, {"error": "invalid JSON"})
        if not isinstance(data, dict):
            return None, self.reply(400, {"error": "invalid JSON"})
        return data, None

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/reset":
            # SECURITY: clearing the cookie must also kill the server-side
            # session — otherwise the old sid value could still act (hub
            # tokens live in chatlib._SESSIONS keyed by "web-"+sid).
            old = self.sid()
            with BRAIN_LOCK:
                chatlib._SESSIONS.pop("web-" + old, None)
            return self.reply(200, {"ok": True}, cookie=self.cookie_attr(0) % "")
        if path == "/api/login":
            return self.api_login()
        if path == "/api/logout":
            return self.api_logout()
        if path == "/api/cancel":
            return self.api_cancel()
        if path == "/api/confirm":
            return self.api_confirm()
        if path != "/api/chat":
            return self.reply(404, {"error": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = -1
        if length < 0 or length > BODY_CAP:
            return self.reply(413, {"error": "body too large"})
        raw = self.rfile.read(length) if length else b""
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/json":
            return self.reply(415, {"error": "expected application/json"})
        try:
            data = json.loads(raw.decode("utf-8"))
            text = data.get("text") if isinstance(data, dict) else None
        except Exception:
            return self.reply(400, {"error": "invalid JSON"})
        if not isinstance(text, str) or not text.strip():
            return self.reply(400, {"error": "missing text"})
        text = text.strip()
        if len(text) > TEXT_CAP:
            return self.reply(400, {"error": "text too long (max %d)" % TEXT_CAP})

        sid = self.sid()
        ok, retry = allow("s:" + sid, burst=8, per_minute=4)
        if not ok:
            return self.reply(
                429, {"error": "rate limited"},
                cookie=self.set_sid(sid), extra={"Retry-After": str(retry)})
        ip = self.client_address[0]
        ok, retry = allow("ip:" + ip, burst=30, per_minute=20)
        if not ok:
            return self.reply(
                429, {"error": "rate limited"},
                cookie=self.set_sid(sid), extra={"Retry-After": str(retry)})

        # one brain, serialized (see docstring)
        try:
            with BRAIN_LOCK:
                reply = chatlib.handle_text(HUB_URL, text, sender="web-" + sid)
                # chat-first UI: hand the board the same results the brain
                # stashed for this sender (raw dicts, 'book <n>' order).
                results = chatlib.last_results("web-" + sid)
        except Exception:
            return self.reply(
                502, {"error": "chat brain error — try again"},
                cookie=self.set_sid(sid))
        # Board-only results (owner call): when the search already opened as
        # UI on the board, don't repeat the ASCII wall in the transcript —
        # speak one short line instead. Only search-result frames are
        # rewritten ('To book one' tail); my-listings/booking frames stay text.
        if results and "╔" in reply and "To book one" in reply:
            reply = ("I found %d match%s — they're open on the board for you. "
                     "Say 'book <n>' to book one, or tell me what to refine."
                     % (len(results), "" if len(results) == 1 else "es"))
        return self.reply(200, {"reply": reply, "results": results}, cookie=self.set_sid(sid))

    # ---- W1: account endpoints (session = sid, tokens stay server-side) ----
    def api_login(self):
        data, err = self.json_body()
        if err:
            return err
        sid = self.sid()
        ok, retry = allow("login:" + sid, burst=5, per_minute=5)
        if not ok:
            return self.reply(429, {"error": "too many login attempts, retry in %ds" % retry},
                              cookie=self.set_sid(sid), extra={"Retry-After": str(retry)})
        seed = str(data.get("seed", "")).strip()
        code = str(data.get("code", "")).strip()
        if not seed and not code:
            return self.reply(400, {"error": "seed or code required"}, cookie=self.set_sid(sid))
        if seed and not re.fullmatch(r"[0-9a-f]{64}", seed.removeprefix("elseed-")):
            return self.reply(400, {"error": "seed must be 64 hex chars (from signup)"}, cookie=self.set_sid(sid))
        if code and len(code) > 128:
            return self.reply(400, {"error": "code too long"}, cookie=self.set_sid(sid))
        # One brain, proven functions: chatlib does the hub calls and mints the
        # session. The reply text is spoken in chat; the UI gets the account id.
        try:
            with BRAIN_LOCK:
                reply = chatlib._login_seed(HUB_URL, "web-" + sid, seed) if seed \
                    else chatlib._login(HUB_URL, "web-" + sid, code)
                sess = chatlib._session("web-" + sid)
        except Exception:
            return self.reply(502, {"error": "hub unreachable"}, cookie=self.set_sid(sid))
        acct = None
        if sess:
            acct = {"account_id": sess.get("account_id"),
                    "verified": bool(sess.get("verified")),
                    "has_payout": bool(sess.get("payout_pk"))}
            # B10 discipline: a failed login must not leave a stale session
            if not sess.get("account_id"):
                chatlib._SESSIONS.pop("web-" + sid, None)
                acct = None
            else:
                # SECURITY: rotate the sid on privilege change (session
                # fixation hardening) — a pre-login sid must not survive into
                # an authenticated session. Move the chatlib session to the
                # new key and hand the browser the new cookie.
                new_sid = secrets.token_urlsafe(24)
                if SID_RE.fullmatch(new_sid):
                    with BRAIN_LOCK:
                        chatlib._SESSIONS["web-" + new_sid] = chatlib._SESSIONS.pop("web-" + sid)
                        # keep positional 'book 1' refs working across login
                        old_stash = chatlib._LAST_RESULTS.pop("web-" + sid, None)
                        if old_stash is not None:
                            chatlib._LAST_RESULTS["web-" + new_sid] = old_stash
                    sid = new_sid
        return self.reply(200, {"ok": acct is not None, "account": acct, "reply": reply},
                          cookie=self.set_sid(sid), extra={"Cache-Control": "no-store"})

    def api_logout(self):
        sid = self.sid()
        with BRAIN_LOCK:
            popped = chatlib._SESSIONS.pop("web-" + sid, None)
        return self.reply(200, {"ok": True, "logged_out": popped is not None},
                          cookie=self.set_sid(sid), extra={"Cache-Control": "no-store"})

    def api_cancel(self):
        """Buyer cancel via the booking-time cancel_token (stashed server-side
        at booking). Token never leaves the server; hub enforces the refund."""
        data, err = self.json_body()
        if err:
            return err
        sid = self.sid()
        bid = str(data.get("booking_id", ""))
        if not BID_RE.fullmatch(bid):
            return self.reply(400, {"error": "booking_id invalid"}, cookie=self.set_sid(sid))
        sess = chatlib._session("web-" + sid)
        tok = ((sess or {}).get("cancel_tokens") or {}).get(bid)
        if not tok:
            return self.reply(403, {"error": "no cancel token for this booking in this session"},
                              cookie=self.set_sid(sid))
        st, res = hub_fetch("/book/%s/cancel" % urllib.parse.quote(bid), token=tok, payload={})
        if st == 0:
            return self.reply(502, {"error": "hub unreachable"}, cookie=self.set_sid(sid))
        if st == 200:
            sess.get("cancel_tokens", {}).pop(bid, None)
        return self.reply(st, {"ok": st == 200, "booking_id": bid, "escrow": res.get("escrow"),
                               "error": res.get("error")}, cookie=self.set_sid(sid))

    def api_confirm(self):
        """Owner confirm for incoming orders: hub /orders proves ownership,
        then a single-use confirm token (hub's documented /admin/tokens path)
        drives the existing /book/{id}/confirm endpoint. I2: no new money path."""
        data, err = self.json_body()
        if err:
            return err
        sid = self.sid()
        bid = str(data.get("booking_id", ""))
        if not BID_RE.fullmatch(bid):
            return self.reply(400, {"error": "booking_id invalid"}, cookie=self.set_sid(sid))
        sess = chatlib._session("web-" + sid)
        list_tok = (sess or {}).get("tokens", {}).get("list")
        if not list_tok:
            return self.reply(401, {"error": "login required"}, cookie=self.set_sid(sid))
        st, res = hub_fetch("/orders", token=list_tok)
        if st == 0:
            return self.reply(502, {"error": "hub unreachable"}, cookie=self.set_sid(sid))
        if st != 200:
            return self.reply(st, {"ok": False, "error": res.get("error", "orders unavailable")},
                              cookie=self.set_sid(sid))
        mine = next((o for o in (res.get("orders") or []) if o.get("id") == bid), None)
        if not mine:
            return self.reply(403, {"error": "booking not in your merchant orders"}, cookie=self.set_sid(sid))
        if mine.get("escrow") not in ("HELD", "WAIVED"):
            return self.reply(409, {"error": "escrow is %s" % mine.get("escrow")}, cookie=self.set_sid(sid))
        # /admin/tokens expects the admin key in the X-Hub-Token header —
        # hub_fetch puts token there, so pass ADMIN_KEY directly.
        st2, mt = hub_fetch("/admin/tokens", token=ADMIN_KEY, payload={"act": "confirm", "booking_id": bid})
        if st2 != 201:
            return self.reply(502, {"error": "could not mint confirm token", "detail": mt.get("error")},
                              cookie=self.set_sid(sid))
        st3, cres = hub_fetch("/book/%s/confirm" % urllib.parse.quote(bid),
                              token=mt.get("token"), payload={})
        if st3 == 0:
            return self.reply(502, {"error": "hub unreachable"}, cookie=self.set_sid(sid))
        return self.reply(st3, {"ok": st3 == 200, "booking_id": bid, "escrow": cres.get("escrow"),
                                "owner_received": cres.get("owner_received"),
                                "error": cres.get("error")}, cookie=self.set_sid(sid))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    srv = ThreadingHTTPServer((BIND, port), Handler)
    srv.daemon_threads = True
    print("[webchat] serving on http://%s:%d (hub: %s)" % (BIND, port, HUB_URL), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
