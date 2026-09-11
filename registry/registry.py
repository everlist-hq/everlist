#!/usr/bin/env python3
"""D2: agent-hub registry service v0 (open + verified tiers).

Authoritative hub-of-hubs index per the master plan. app.py's built-in
/registry endpoint is DEMO-ONLY; this service is the real one.

Endpoints:
  GET  /hubs            -> {open: [...], verified: [...]}
  GET  /registry.json   -> C6 SIGNED bootstrap document {format, payload,
                           signature}: ed25519 over the canonical payload
                           bytes (sort_keys + tight separators); payload
                           carries hubs + per-hub protocol/api_contract
  GET  /registry.pub    -> signing public key (out-of-band pinning)
  GET  /hubs/{hub_id}   -> record + (if hub exposes ledger) self-reported totals
  POST /register        -> {url}; ownership proof: hub must echo a fresh nonce
                           at /challenge?nonce=... (challenge_response field),
                           then manifest at /.well-known/agent-hub.json is
                           schema-validated. Self-registration can NEVER claim
                           tier=verified.

Persistence: registry.json (atomic tmp + os.replace), survives restarts.
Signing key: registry-signing.key (0600, gitignored, persistent; override
via REGISTRY_SIGNING_KEY). Verify with: check_hub.py --registry <url>

SSRF-safe fetch policy (plan D2):
  - https-only outside dev mode (REGISTRY_DEV=1 relaxes to http for localhost)
  - no credentials in URLs (incl. redirects)
  - destination IP checked at resolve time: private/loopback/link-local blocked
    (dev mode allows loopback only)
  - redirects revalidated (max 3)
  - bounded DNS/connect/read time and bounded response bytes
  - manifest schema-validated before storage

Port: HUB_REGISTRY_PORT (default 8810).
"""
import ipaddress
import http.client
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.environ.get("REGISTRY_STATE", os.path.join(HERE, "registry.json"))
PORT = int(os.environ.get("HUB_REGISTRY_PORT", "8810"))
DEV = os.environ.get("REGISTRY_DEV", "0") == "1"
MAX_BYTES = 64 * 1024
TIMEOUT = 5.0
MAX_REDIRECTS = 3

_LOCK = threading.Lock()
REGISTRY = {"open": [], "verified": []}

# C6: signed registry document - Ed25519 keypair (persistent, 0600, gitignored
# via *.key). /registry.json serves {format, payload, signature}; the signature
# covers the EXACT canonical JSON bytes of payload (sort_keys + tight
# separators) so verifiers need no canonicalization logic beyond json.dumps.
KEY_FILE = os.environ.get("REGISTRY_SIGNING_KEY",
                          os.path.join(HERE, "registry-signing.key"))
_SIGN_SK = None
_SIGN_PUB_HEX = None


def _load_or_create_signing_key():
    global _SIGN_SK, _SIGN_PUB_HEX
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE) as f:
            _SIGN_SK = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(f.read().strip()))
        print(f"C6: signing key loaded from {KEY_FILE}")
    else:
        _SIGN_SK = Ed25519PrivateKey.generate()
        raw = _SIGN_SK.private_bytes(serialization.Encoding.Raw,
                                     serialization.PrivateFormat.Raw,
                                     serialization.NoEncryption())
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(raw.hex())
        print(f"C6: new signing key generated at {KEY_FILE}")
    _SIGN_PUB_HEX = _SIGN_SK.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def canonical_payload_bytes(payload) -> bytes:
    """THE canonical form: sort_keys + tight separators. Signer and every
    verifier use exactly this - no drift possible."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def registry_document() -> dict:
    """C6: the signed, agent-readable bootstrap document (hubs + versions)."""
    from cryptography.hazmat.primitives import serialization
    hubs = []
    with _LOCK:
        for tier in ("open", "verified"):
            for r in REGISTRY[tier]:
                man = r.get("manifest", {}) or {}
                hubs.append({"hub_id": r["hub_id"], "url": r["url"], "tier": r["tier"],
                             "registered": r.get("registered"),
                             "protocol": man.get("protocol"),
                             "api_contract": man.get("api_contract"),
                             "content_policy": man.get("content_policy")})
    payload = {"format": "everlist-registry/1",
               "generated_unix": int(time.time()),
               "hub_count": len(hubs),
               "hubs": hubs,
               "doc": "signature: ed25519 over canonical payload bytes; verify with check_hub.py --registry"}
    sig = _SIGN_SK.sign(canonical_payload_bytes(payload)).hex()
    return {"format": "everlist-registry/1-signed",
            "payload": payload,
            "signature": {"algo": "ed25519", "public_key": _SIGN_PUB_HEX, "sig": sig}}


def _load():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                REGISTRY.update(json.load(f))
            print(f"D2: restored {len(REGISTRY['open'])} open + "
                  f"{len(REGISTRY['verified'])} verified hubs")
        except Exception as ex:
            print(f"D2 WARNING: corrupt registry state ({ex}); starting fresh")


def _persist():
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(REGISTRY, f)
    os.replace(tmp, STATE_FILE)


class FetchError(Exception):
    pass


def _check_ip(host: str):
    """Resolve and check destination at resolve time (SSRF policy)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as ex:
        raise FetchError(f"dns failure: {ex}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved) \
                and not (DEV and ip.is_loopback):
            raise FetchError(f"blocked destination: {ip} (private/loopback/link-local)")


def fetch_json(url: str, redirects: int = 0) -> dict:
    """SSRF-safe bounded JSON fetch with redirect revalidation."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "https":
        pass
    elif parsed.scheme == "http":
        if not (DEV and parsed.hostname in ("localhost", "127.0.0.1")):
            raise FetchError("http only allowed in dev mode for localhost")
    else:
        raise FetchError(f"unsupported scheme: {parsed.scheme}")
    if parsed.username or parsed.password:
        raise FetchError("credentials in URL not allowed")
    _check_ip(parsed.hostname)

    req = urllib.request.Request(url, headers={"User-Agent": "agent-hub-registry/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError("response exceeds byte budget")
            if resp.status != 200:
                raise FetchError(f"http {resp.status}")
            final = resp.geturl()
    except urllib.error.HTTPError as ex:
        if ex.code in (301, 302, 303, 307, 308):
            if redirects >= MAX_REDIRECTS:
                raise FetchError("too many redirects")
            loc = ex.headers.get("Location", "")
            return fetch_json(urllib.parse.urljoin(url, loc), redirects + 1)
        raise FetchError(f"http {ex.code}")
    except (urllib.error.URLError, http.client.HTTPException, OSError) as ex:
        # HTTPException covers RemoteDisconnected etc. raised RAW by http.client
        raise FetchError(f"fetch failed: {ex}")
    if final != url:
        p2 = urllib.parse.urlparse(final)
        if p2.scheme not in ("https", "http"):
            raise FetchError("redirect to unsupported scheme")
        if p2.username or p2.password:
            raise FetchError("credentials in redirect URL")
        if not (DEV and p2.hostname in ("localhost", "127.0.0.1")):
            _check_ip(p2.hostname)  # revalidate redirect destination
    try:
        return json.loads(raw.decode())
    except Exception as ex:
        raise FetchError(f"invalid json: {ex}")


def validate_manifest(man) -> tuple:
    """Schema-validate a hub manifest before storage (D2; matches the REAL
    agent-hub manifest shape: hub/protocol/payments/capabilities)."""
    if not isinstance(man, dict):
        return False, "manifest not an object"
    for key in ("hub", "protocol", "payments", "capabilities"):
        if key not in man:
            return False, f"missing required key: {key}"
    if not isinstance(man.get("hub"), str) or not man["hub"].strip():
        return False, "hub must be a non-empty string"
    if not isinstance(man.get("protocol"), str):
        return False, "protocol must be a string"
    if not isinstance(man.get("payments"), dict):
        return False, "payments must be an object"
    if not isinstance(man.get("capabilities"), dict):
        return False, "capabilities must be an object"
    return True, ""


def ledger_url(rec: dict) -> str | None:
    """Resolve the hub's public ledger endpoint from its manifest.
    fairness.ledger is a hub-relative path (e.g. '/ledger')."""
    path = rec.get("manifest", {}).get("fairness", {}).get("ledger")
    if isinstance(path, str) and path.startswith("/"):
        return rec["url"].rstrip("/") + path
    if isinstance(path, str) and path.startswith("http"):
        return path
    return None


def prove_ownership(url: str) -> tuple:
    """Hub must echo a fresh nonce at /challenge?nonce=... (proves URL control)."""
    nonce = os.urandom(8).hex()
    try:
        resp = fetch_json(f"{url.rstrip('/')}/challenge?nonce={nonce}")
    except FetchError as ex:
        return False, f"challenge fetch failed: {ex}"
    if isinstance(resp, dict) and resp.get("challenge_response") == nonce:
        return True, ""
    return False, "challenge_response mismatch (ownership not proven)"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        if u.path == "/registry.json":
            # C6: the signed, agent-readable bootstrap document
            return self._json(200, registry_document())
        if u.path == "/registry.pub":
            # C6: signing public key (hex) for out-of-band pinning
            return self._json(200, {"algo": "ed25519", "public_key": _SIGN_PUB_HEX,
                "note": "pin this out-of-band for production; /registry.json also embeds it"})
        if u.path == "/hubs":
            with _LOCK:
                return self._json(200, {"open": list(REGISTRY["open"]),
                                         "verified": list(REGISTRY["verified"]),
                                         "note": "verified requires operator review - "
                                                 "self-registration can never claim it"})
        if u.path.startswith("/hubs/"):
            hid = u.path[len("/hubs/"):]
            with _LOCK:
                rec = next((r for r in REGISTRY["open"] + REGISTRY["verified"]
                            if r["hub_id"] == hid), None)
            if rec is None:
                return self._json(404, {"error": "unknown hub"})
            out = {**rec}
            led_ep = ledger_url(rec)
            if led_ep:
                try:
                    led = fetch_json(led_ep)
                    out["ledger_totals"] = led.get("totals")
                    out["ledger_check"] = "retrieved (self-reported; D3 does independent verification)"
                except FetchError as ex:
                    out["ledger_check"] = f"unavailable: {ex}"
            return self._json(200, out)
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if urllib.parse.urlparse(self.path).path != "/register":
            return self._json(404, {"error": "not found"})
        try:
            ln = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(min(ln, 16384)) or b"{}")
        except Exception:
            return self._json(400, {"error": "invalid json"})
        url = (body.get("url") or "").rstrip("/")
        if not url.startswith(("http://", "https://")):
            return self._json(400, {"error": "url required (http/https)"})
        if any(c in url for c in ("@", " ")):
            return self._json(400, {"error": "url contains disallowed characters"})

        ok, why = prove_ownership(url)
        if not ok:
            return self._json(400, {"error": f"ownership proof failed: {why}"})
        try:
            man = fetch_json(f"{url}/.well-known/agent-hub.json")
        except FetchError as ex:
            return self._json(400, {"error": f"manifest fetch failed: {ex}"})
        valid, why = validate_manifest(man)
        if not valid:
            return self._json(400, {"error": f"invalid manifest: {why}"})

        hid = "hub-" + os.urandom(6).hex()
        rec = {"hub_id": hid, "url": url, "tier": "open",
               "registered": time.time(), "manifest": man}
        with _LOCK:
            REGISTRY["open"].append(rec)
            _persist()
        return self._json(201, {"hub_id": hid, "tier": "open",
                                 "note": "tier=verified requires operator review; never self-claimable"})


if __name__ == "__main__":
    _load_or_create_signing_key()  # C6: before serving - key exists by first request
    _load()
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"D2 registry v0 on http://127.0.0.1:{PORT} (dev={DEV})")
    srv.serve_forever()
