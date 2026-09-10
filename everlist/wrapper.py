"""uAgent wrapper for agent-hub-v2 (uagents 0.25.x API) — B4 hardened.

Env config:
- HUB_AGENT_SEED (REQUIRED, fail-closed): the agent's persistent seed.
  WARNING: seeds checked into repos are PUBLIC. A public seed must never hold
  real funds or identity - use a fresh private seed for anything real.
- HUB_URL (default http://localhost:8802)
- HUB_LOG_FILE (default wrapper.log), HUB_LOG_MAX_BYTES (default 1_000_000),
  HUB_LOG_BACKUPS (default 2) — bounded rotating logs.

Log hygiene (B4): tokens, secrets, PII, query strings and full bodies are NEVER
logged; redact() scrubs anything that looks like a credential before logging.
Agents receive a SAFE error envelope (generic message + code); full detail stays
in local logs only.

Run:  HUB_AGENT_SEED=<your-seed> venv/bin/python wrapper.py
"""
import json
import logging
import logging.handlers
import os
import re
import sys

import httpx
from uagents import Agent, Context, Model, Protocol

# ---- B4: fail-closed seed (a checked-in default would be a public credential) ----
SEED = os.environ.get("HUB_AGENT_SEED", "")
if not SEED:
    sys.stderr.write(
        "FATAL: HUB_AGENT_SEED is required.\n"
        "  Example: HUB_AGENT_SEED='<random-long-string>' venv/bin/python wrapper.py\n"
        "  NOTE: a seed stored in a repo is PUBLIC - never attach funds/identity to it.\n")
    sys.exit(78)

HUB_URL = os.environ.get("HUB_URL", "http://localhost:8802")
TIMEOUT = 10.0
LOG_FILE = os.environ.get("HUB_LOG_FILE", "wrapper.log")
LOG_MAX = int(os.environ.get("HUB_LOG_MAX_BYTES", "1000000"))
LOG_BACKUPS = int(os.environ.get("HUB_LOG_BACKUPS", "2"))

# B4: bounded rotating log with redaction of credential-shaped strings
_CRED = re.compile(
    r"(?i)((x-hub-token|authorization|x-payment)['\": =]+\S+)|"
    r"(bk-[0-9a-f]{16,})|(0x[a-fA-F0-9]{40,})|((secret|token|password)['_\": =]+\S+)")


def redact(text: str) -> str:
    return _CRED.sub("[REDACTED]", str(text)[:300])


def _setup_logging():
    handler = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=LOG_MAX, backupCount=LOG_BACKUPS)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # route uagents/httpx INFO (which can echo request lines) through the same cap
    logging.getLogger("uagents").addHandler(handler)
    logging.getLogger("httpx").addHandler(handler)


_setup_logging()
log = logging.getLogger("wrapper")


class HubRequest(Model):
    action: str                 # "search" | "book" | "manifest"
    q: str = ""
    listing_id: str = ""
    attendee: str = ""
    human_verified: bool = False


class HubResponse(Model):
    result: str


hub_agent = Agent(
    name="agent-hub",
    port=8010,
    seed=SEED,
    mailbox=True,  # B3: Agentverse relay — localhost agent becomes reachable via Agentverse
    network="testnet",
)

from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

proto = Protocol(name="hub-access", version="0.2")
chat_proto = Protocol(spec=chat_protocol_spec)  # B3b: ASI:One chat (Agentverse discovery)

_http: httpx.AsyncClient | None = None
_book_token: str | None = None


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=TIMEOUT)
    return _http


@hub_agent.on_event("startup")
async def _startup(ctx: Context):
    log.info("wrapper starting; hub=%s agent=%s", HUB_URL, redact(hub_agent.name))
    _client()


@hub_agent.on_event("shutdown")
async def _shutdown(ctx: Context):
    global _http
    if _http is not None and not _http.is_closed:
        await _http.aclose()
    log.info("wrapper shut down; client closed")


async def _bootstrap_token(ctx: Context) -> str:
    """I2: books as its own principal; bootstraps a reusable book token (cached)."""
    global _book_token
    if _book_token:
        return _book_token
    c = _client()
    r = await c.post(f"{HUB_URL}/access", json={"agent": "agent-hub-wrapper", "acts": ["book"]})
    r.raise_for_status()
    _book_token = r.json()["tokens"]["book"]
    log.info("book token bootstrapped (value never logged)")  # B4: raw tokens are credentials
    return _book_token


# B4: safe envelope codes - generic to the sender, detail only in local logs
_SAFE = {
    httpx.TimeoutException: ("UPSTREAM_TIMEOUT", "hub did not respond in time"),
    httpx.ConnectError: ("UPSTREAM_UNREACHABLE", "hub is not reachable"),
    httpx.HTTPStatusError: ("UPSTREAM_ERROR", "hub returned an error status"),
    httpx.HTTPError: ("UPSTREAM_ERROR", "hub request failed"),
}


async def _call_hub(ctx: Context, coro) -> str:
    """B4: errors become SAFE envelopes (generic message + code); detail -> local log."""
    try:
        r = await coro
        try:
            body = r.json()
        except Exception:
            body = {"error": "non-JSON upstream response"}
        return json.dumps(body)
    except Exception as ex:
        code, msg = _SAFE.get(type(ex), ("WRAPPER_ERROR", "unexpected wrapper error"))
        for cls, (c2, m2) in _SAFE.items():  # exact-match subclasses first pass
            if isinstance(ex, cls):
                code, msg = c2, m2
                break
        log.warning("upstream failure code=%s detail=%s", code, redact(repr(ex)))
        return json.dumps({"error": msg, "code": code})


@proto.on_message(HubRequest, replies=HubResponse)
async def handle(ctx: Context, sender: str, msg: HubRequest):
    c = _client()
    if msg.action == "search":
        result = await _call_hub(ctx, c.get(f"{HUB_URL}/search", params={"q": msg.q}))
    elif msg.action == "book":
        payload = {
            "listing_id": msg.listing_id,
            "attendee": msg.attendee or "unknown-via-uagent",
            "human_verified": msg.human_verified,
        }
        try:
            tok = await _bootstrap_token(ctx)
            result = await _call_hub(ctx, c.post(f"{HUB_URL}/book", json=payload,
                                                 headers={"X-Hub-Token": tok}))
        except httpx.HTTPError as ex:
            log.warning("bootstrap failure detail=%s", redact(repr(ex)))
            result = json.dumps({"error": "hub is not reachable", "code": "UPSTREAM_UNREACHABLE"})
    elif msg.action == "manifest":
        result = await _call_hub(ctx, c.get(f"{HUB_URL}/.well-known/agent-hub.json"))
    else:
        result = json.dumps({"error": "unknown action", "code": "BAD_REQUEST",
                             "known": ["search", "book", "manifest"]})
    await ctx.send(sender, HubResponse(result=result))


hub_agent.include(proto)


# ---- B3b: ASI:One chat (Agentverse discovery) ----------------------------
# Deterministic hub-backed chat: search / booking guidance / fee transparency.
# Intent logic lives in chatlib.py (unit-tested); no external LLM required.


@chat_proto.on_message(ChatAcknowledgement)
async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    ctx.logger.info(f"chat ack from {redact(sender)} for {msg.acknowledged_msg_id}")


@chat_proto.on_message(ChatMessage)
async def handle_chat(ctx: Context, sender: str, msg: ChatMessage):
    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=msg.timestamp, acknowledged_msg_id=msg.msg_id),
    )
    text = msg.text().strip()
    if not text:
        return
    log.info("chat from %s: %s", redact(sender), redact(text[:80]))
    try:
        reply_text = await asyncio.to_thread(chatlib.handle_text, HUB_URL, text, sender)
    except Exception as ex:  # B4: safe envelope even for chatlib bugs
        log.warning("chat failure detail=%s", redact(repr(ex)))
        reply_text = "Sorry — something went wrong on my side. Please try again."
    await ctx.send(sender, ChatMessage([TextContent(type="text", text=reply_text)]))


hub_agent.include(chat_proto)

if __name__ == "__main__":
    hub_agent.run()
