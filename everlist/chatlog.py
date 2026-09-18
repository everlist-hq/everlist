"""Chat turn log (JSONL) — owner call 2026-09-18: keep every top-level chat
message + reply to improve the chat.

Laws:
- NEVER break the chat: every failure inside here is swallowed.
- NEVER store secrets: signup seeds (64-hex), login seeds, pvt- deal claims
  and acct- login codes are redacted before anything hits disk.
- Sender privacy: only a 12-char sha256 prefix of the sender id is stored.
- Rotation: chatlog.jsonl -> chatlog.jsonl.1 at 10 MB (one generation kept).
- Off switch: EVERLIST_CHATLOG=off|0|false|none disables writing entirely.

Line shape:
  {"ts": iso8601-utc, "surface": "web|agent|other", "sender": "12hex",
   "ms": 1234.5, "ok": true, "text": "...", "reply": "...",
   "brain": {"provider": "primary|fallback|none", "action": "search", "ms": 900}}
"""
import hashlib
import json
import os
import re
import threading
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_LOG_PATH = os.environ.get("EVERLIST_CHATLOG",
                           os.path.join(_HERE, ".run", "chatlog.jsonl"))
_MAX_BYTES = 10 * 1024 * 1024

_tls = threading.local()   # per-thread nesting depth for handle_text re-entries

# secrets that must never reach disk
_SEED_RX = re.compile(r"\b[0-9a-fA-F]{64}\b")            # signup/login seed
_ACCT_RX = re.compile(r"\bacct-[0-9a-f]{8}\b")           # account login code
_CLAIM_RX = re.compile(r"\bpvt-[0-9a-f]{16}\b")          # private-deal claim


def _redact(text: str) -> str:
    if not text:
        return ""
    s = _SEED_RX.sub("[seed-redacted]", text)
    s = _ACCT_RX.sub("[acct-redacted]", s)
    s = _CLAIM_RX.sub("[claim-redacted]", s)
    return s


def _sender_hash(sender: str) -> str:
    return hashlib.sha256((sender or "").encode("utf-8", "replace")).hexdigest()[:12]


def surface(sender: str) -> str:
    s = sender or ""
    if s.startswith("web-"):
        return "web"
    if s.startswith("agent1q") or s.startswith("agent"):
        return "agent"
    return "other"


def depth_inc() -> int:
    """Increment nesting depth, return the PREVIOUS depth (0 = top-level turn)."""
    d = getattr(_tls, "d", 0)
    _tls.d = d + 1
    return d


def depth_dec() -> None:
    _tls.d = max(0, getattr(_tls, "d", 1) - 1)


def _brain_info():
    """Best-effort peek at what the brain did on this thread's last LLM call."""
    try:
        import brain
        info = getattr(brain, "LAST", None)
        if isinstance(info, dict):
            return {"provider": info.get("provider"), "action": info.get("action"),
                    "ms": round(float(info.get("ms") or 0), 1)}
    except Exception:
        pass
    return None


def log_turn(sender: str, text: str, reply: str, ms: float,
             ok: bool = True, error=None) -> None:
    """Append one top-level turn. Never raises. Off-switchable via env."""
    if os.environ.get("EVERLIST_CHATLOG", "").strip().lower() in ("off", "0", "false", "none"):
        return
    try:
        path = _LOG_PATH
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            if os.path.getsize(path) > _MAX_BYTES:
                os.replace(path, path + ".1")
        except OSError:
            pass
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "surface": surface(sender),
            "sender": _sender_hash(sender),
            "ms": round(float(ms), 1),
            "ok": bool(ok),
            "text": _redact(str(text or ""))[:2000],
            "reply": _redact(str(reply or ""))[:2000],
        }
        if error is not None:
            rec["error"] = ("%s: %s" % (type(error).__name__, error))[:300]
        info = _brain_info()
        if info is not None:
            rec["brain"] = info
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # logging must never take the chat down
