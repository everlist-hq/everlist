"""Shared signed-token helpers for agent-hub-v2 (plan tasks I2 + G5).

Token format (v1, domain-separated):
    token := b64url(canonical_json(payload)) "." hex(HMAC_SHA256(key, DOMAIN + body))
    payload := {"v":1, "act": action, "sub": principal, "exp": unix_ts, "nonce": hex}

Canonical encoding: json.dumps(sort_keys=True, separators=(",",":")).
Replay protection: nonce single-use (verified against server-side registry).
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time

TOKEN_DOMAIN = "agenthub-token-v1:"

_NONCES = {}          # nonce -> exp (replay registry, pruned lazily)
_NLOCK = threading.Lock()


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def mint_token(key: str, action: str, principal: str, ttl: int = 3600) -> str:
    payload = {"v": 1, "act": action, "sub": principal,
               "exp": int(time.time()) + ttl, "nonce": secrets.token_hex(8)}
    body = _b64e(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    sig = hmac.new(key.encode(), (TOKEN_DOMAIN + body).encode(), hashlib.sha256).hexdigest()
    return body + "." + sig


def verify_token(key: str, token: str, action: str, single_use: bool = True,
                 subject: str = None):
    """Return (payload, None) on success, (None, reason) on any failure.
    subject: if given, must equal payload['sub'] — checked BEFORE nonce consumption
    so failed authorization never burns a token."""
    try:
        body, sig = token.strip().split(".", 1)
        payload = json.loads(base64.urlsafe_b64decode(body + "==").decode())
        if payload.get("v") != 1:
            return None, "unsupported token version"
        expected = hmac.new(key.encode(), (TOKEN_DOMAIN + body).encode(),
                            hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None, "bad signature"
        if payload.get("act") != action:
            return None, f"wrong action: token is for '{payload.get('act')}'"
        if int(payload.get("exp", 0)) < time.time():
            return None, "token expired"
        nonce = payload.get("nonce", "")
        if subject is not None and payload.get("sub") != subject:
            return None, "token not valid for this subject"
        if single_use:
            with _NLOCK:
                if nonce in _NONCES:
                    return None, "replayed token (nonce already used)"
                _NONCES[nonce] = payload["exp"]
                for n in [n for n, e in _NONCES.items() if e < time.time()]:
                    _NONCES.pop(n, None)  # lazy prune
        return payload, None
    except Exception as ex:  # malformed tokens must never crash the route
        return None, f"malformed token: {ex}"
