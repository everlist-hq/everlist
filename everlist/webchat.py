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
import urllib.request
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

PORT = int(os.environ.get("WEBCHAT_PORT", "8804"))
BIND = os.environ.get("WEBCHAT_BIND", "127.0.0.1")
HUB_URL = os.environ.get("WEBCHAT_HUB_URL", "http://localhost:8802")
STATIC_DIR = os.path.join(HERE, "static")
BODY_CAP = 32 * 1024          # bytes; matches hub 413 discipline
TEXT_CAP = 8000               # chars per message (UI maxlength mirrors this)
COOKIE = "sid"
SESSION_MAX_AGE = 24 * 3600   # aligned with chatlib session TTL
SID_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")

# chatlib brain serialization (see docstring)
BRAIN_LOCK = threading.Lock()

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
        else:
            body, ctype = static_bytes(path.lstrip("/"))
        if body is None:
            return self.reply(404, {"error": "not found"})
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.common_headers()
        if ctype.startswith("text/html"):
            self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def api_get(self, path):
        if path == "/api/health":
            return self.reply(200, {"ok": hub_ok(), "hub": HUB_URL})
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
        return self.reply(404, {"error": "not found"})

    # ---- POST ----
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/reset":
            return self.reply(200, {"ok": True}, cookie=self.cookie_attr(0) % "")
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
        except Exception:
            return self.reply(
                502, {"error": "chat brain error — try again"},
                cookie=self.set_sid(sid))
        return self.reply(200, {"reply": reply}, cookie=self.set_sid(sid))


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
