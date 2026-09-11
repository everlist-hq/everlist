"""B6: chat-protocol E2E through the REAL wrapper uAgent (backlog B6).

Deterministic version (lessons from the 65-char root-cause hunt):
  - one-shot sends (no interval re-fire double-signups)
  - tolerant to out-of-order / duplicate replies
  - the wrapper runs IN-PROCESS: the test inspects wrapper's chatlib._SESSIONS
    directly to prove session persistence across messages (root-cause tool)

Flow: sender A signup (seed shown once, auto-login) -> one-prompt list
      (owned by the ACCOUNT) -> sender B login-seed (same account, 2nd chat)
      -> codeless edit. Asserts replies + hub state.json ownership.
Run: ./venv/bin/python test_chat_e2e.py
"""
import asyncio
import atexit
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-chat-e2e-")
STATE = os.path.join(TMP, "state.json")
LOGF = os.path.join(TMP, "hub.log")
_ACTIVE = []


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


atexit.register(lambda: [_kill(p) for p in _ACTIVE if p])


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


log_fh = open(LOGF, "w")
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_EMAIL_MODE": "log",
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=log_fh, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"

# Regression (B6 root cause): REAL uAgent addresses are 65+ chars — the hub must
# accept them everywhere (this exact check failed at 64 before the fix).
sys.path.insert(0, HERE)
import chatlib as _cl  # noqa: E402

_long_agent = "agent1q" + "a" * 58  # 65 chars, bech32-shaped
_st65, _res65 = _cl._hub_post(
    f"http://127.0.0.1:{PORT}", "/accounts/signup",
    {"agent": _long_agent, "pubkey": _cl._pubkey_of(os.urandom(32).hex()),
     "pow": _cl._pow_solve(f"http://127.0.0.1:{PORT}", "signup")})
assert _st65 == 201, f"65-char agent signup must succeed, got {_st65}: {_res65}"
print("PASS B6-regression: 65-char agent address accepted by hub")
sys.stdout.flush()

# import the wrapper embedded (unique test seed; tmp log; fresh-hub URL)
os.environ["HUB_AGENT_SEED"] = f"chat-e2e-wrapper-{uuid.uuid4().hex[:12]}"
os.environ["HUB_LOG_FILE"] = os.path.join(TMP, "wrapper.log")
os.environ["HUB_URL"] = f"http://127.0.0.1:{PORT}"
spec = importlib.util.spec_from_file_location("wrapper", os.path.join(HERE, "wrapper.py"))
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)
WRAPPER_ADDR = w.hub_agent.address

# ---- call-level trace: watch every session lookup/store inside the wrapper ----
_orig_session = w.chatlib._session
_orig_set = w.chatlib._set_session


def _traced_session(sender):
    r = _orig_session(sender)
    print(f"[TRACE _session] t={time.time():.3f} sender={sender!r} -> "
          + (f"HIT acct={(r or {}).get('account_id')}" if r else "MISS"))
    sys.stdout.flush()
    return r


def _traced_set(sender, res):
    print(f"[TRACE _set_session] t={time.time():.3f} sender={sender!r} acct={res.get('account_id')}")
    sys.stdout.flush()
    return _orig_set(sender, res)


w.chatlib._session = _traced_session
w.chatlib._set_session = _traced_set

# trace EVERY handle_text entry: full sender + length (the microscope)
_orig_ht = w.chatlib.handle_text


def _traced_handle_text(hub_url, text, sender=""):
    print(f"[TRACE handle_text] t={time.time():.3f} sender_len={len(sender)} "
          f"sender={sender!r} text={text[:24]!r}")
    sys.stdout.flush()
    return _orig_ht(hub_url, text, sender)


w.chatlib.handle_text = _traced_handle_text

from uagents import Agent, Bureau, Context, Protocol  # noqa: E402
from uagents_core.contrib.protocols.chat import (  # noqa: E402
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)

RESULTS = {"seed": None, "listing_id": None, "account_id": None, "signup": None,
           "list": None, "login_seed": None, "edit": None, "whoami": None,
           "archive": None, "unarchive": None}
PROBES = {}
SENT = {"signup": False, "login_seed": False, "edit": False,
        "whoami": False, "archive": False, "unarchive": False}
DONE = asyncio.Event()


def make_text(text: str) -> ChatMessage:
    return ChatMessage(timestamp=datetime.now(timezone.utc),
                       msg_id=uuid.uuid4(),
                       content=[TextContent(type="text", text=text)])


def reply_text(msg) -> str:
    return "\n".join(c.text for c in msg.content if isinstance(c, TextContent))


def probe(tag: str):
    """Ground truth, bypassing the wrapper: hub disk state + the wrapper's OWN
    session store (same interpreter — this is the root-cause microscope)."""
    try:
        snap = json.loads(open(STATE).read())
        accs = snap.get("accounts", {})
        n_acc = len(accs)
        owner = next((l.get("owner") for l in snap.get("listings", [])
                      if l.get("title") == "Chat E2E Gig"), None)
    except Exception as ex:
        accs, n_acc, owner = {}, -1, f"ERR:{ex}"[:60]
    sess = getattr(w.chatlib, "_SESSIONS", {})
    try:
        mine = sess.get(clientA.address)
        mine_state = "present" if mine else "MISSING"
        mine_acct = (mine or {}).get("account_id")
    except Exception as ex:
        mine_state, mine_acct = f"ERR:{ex}"[:60], None
    if tag == "after-signup":
        print(f"[ADDR] clientA={clientA.address!r} len={len(clientA.address)} | "
              f"clientB={clientB.address!r} len={len(clientB.address)} | "
              f"wrapper={WRAPPER_ADDR!r} len={len(WRAPPER_ADDR)}")
        sys.stdout.flush()
    PROBES[tag] = {"n_acc": n_acc, "owner": owner, "mine": mine_state,
                   "mine_acct": mine_acct, "sess_keys": len(sess)}
    print(f"[PROBE {tag}] hub_accounts={n_acc} listing_owner={str(owner)[:24]} "
          f"wrapper_session_for_A={mine_state}({mine_acct}) sessions_total={len(sess)}")
    sys.stdout.flush()


_run = uuid.uuid4().hex[:8]
clientA = Agent(name=f"chat-e2e-a-{_run}", seed=f"chat-e2e-client-a-{_run}")
clientB = Agent(name=f"chat-e2e-b-{_run}", seed=f"chat-e2e-client-b-{_run}")
protoA = Protocol(spec=chat_protocol_spec)
protoB = Protocol(spec=chat_protocol_spec)


@protoA.on_message(ChatMessage)
async def on_reply_a(ctx: Context, sender: str, msg: ChatMessage):
    reply = reply_text(msg)
    if RESULTS["signup"] is None:
        RESULTS["signup"] = reply
        m = re.search(r"\b([0-9a-f]{64})\b", reply)
        if m:
            RESULTS["seed"] = m.group(1)
            probe("after-signup")
            await ctx.send(sender, make_text("list Chat E2E Gig | meetup | 2026-10-01 | 5 | x | 10"))
        else:
            DONE.set()
    elif "is live" in reply and RESULTS["list"] is None:
        RESULTS["list"] = reply
        m = re.search(r"is live \(id: (even-\d+)\)", reply)
        if m:
            RESULTS["listing_id"] = m.group(1)
        probe("after-list")


@protoA.on_message(ChatAcknowledgement)
async def on_ack_a(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@protoB.on_message(ChatMessage)
async def on_reply_b(ctx: Context, sender: str, msg: ChatMessage):
    reply = reply_text(msg)
    if RESULTS["login_seed"] is None:
        RESULTS["login_seed"] = reply
        m = re.search(r"account (acct-[0-9a-f]+)", reply)
        if m:
            RESULTS["account_id"] = m.group(1)
        if RESULTS["listing_id"] and not SENT["edit"]:
            SENT["edit"] = True
            await ctx.send(sender, make_text(f"edit {RESULTS['listing_id']} price: 7"))
        else:
            DONE.set()
    elif RESULTS["edit"] is None:
        RESULTS["edit"] = reply
        if not SENT["whoami"]:
            SENT["whoami"] = True
            await ctx.send(sender, make_text("whoami"))
        else:
            DONE.set()
    elif RESULTS["whoami"] is None:
        RESULTS["whoami"] = reply
        if RESULTS["listing_id"] and not SENT["archive"]:
            SENT["archive"] = True
            await ctx.send(sender, make_text(f"archive {RESULTS['listing_id']}"))
        else:
            DONE.set()
    elif RESULTS["archive"] is None:
        RESULTS["archive"] = reply
        try:
            lst = next(x for x in json.loads(open(STATE).read())["listings"]
                       if x["id"] == RESULTS["listing_id"])
            PROBES["after-archive"] = {"archived": bool(lst.get("archived"))}
        except Exception as ex:
            PROBES["after-archive"] = {"err": str(ex)[:60]}
        print(f"[PROBE after-archive] {PROBES['after-archive']}")
        sys.stdout.flush()
        if not SENT["unarchive"]:
            SENT["unarchive"] = True
            await ctx.send(sender, make_text(f"unarchive {RESULTS['listing_id']}"))
        else:
            DONE.set()
    elif RESULTS["unarchive"] is None:
        RESULTS["unarchive"] = reply
        try:
            lst = next(x for x in json.loads(open(STATE).read())["listings"]
                       if x["id"] == RESULTS["listing_id"])
            PROBES["after-unarchive"] = {"archived": bool(lst.get("archived"))}
        except Exception as ex:
            PROBES["after-unarchive"] = {"err": str(ex)[:60]}
        print(f"[PROBE after-unarchive] {PROBES['after-unarchive']}")
        sys.stdout.flush()
        DONE.set()


@protoB.on_message(ChatAcknowledgement)
async def on_ack_b(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


@clientA.on_interval(period=3.0)
async def kick(ctx: Context):
    if not SENT["signup"]:
        SENT["signup"] = True  # ONE-SHOT: no double signups
        await ctx.send(WRAPPER_ADDR, make_text("signup"))


@clientB.on_interval(period=2.0)
async def follow(ctx: Context):
    if not SENT["login_seed"] and RESULTS["seed"] and RESULTS["listing_id"]:
        SENT["login_seed"] = True  # ONE-SHOT
        await ctx.send(WRAPPER_ADDR, make_text(f"login-seed {RESULTS['seed']}"))


clientA.include(protoA)
clientB.include(protoB)


async def main():
    bureau = Bureau()
    bureau.add(w.hub_agent)
    bureau.add(clientA)
    bureau.add(clientB)
    asyncio.ensure_future(bureau.run_async())  # uagents 0.25 async entrypoint
    try:
        await asyncio.wait_for(DONE.wait(), timeout=120)
    except asyncio.TimeoutError:
        print(f"CHAT_E2E_TIMEOUT: results={json.dumps(RESULTS, default=str)[:600]}")
        sys.stdout.flush()
        _kill(proc)
        os._exit(1)  # hard exit inside loop: never cancel Bureau (teardown recursion)
    _kill(proc)  # hub stopped: state.json final

    checks = []

    def chk(name, cond, detail=""):
        checks.append((name, bool(cond)))
        print(("PASS" if cond else "FAIL"), name, detail)

    r_signup = RESULTS["signup"] or ""
    r_list = RESULTS["list"] or ""
    r_login = RESULTS["login_seed"] or ""
    r_edit = RESULTS["edit"] or ""

    chk("B6 signup reply shows seed once", "SEED" in r_signup and bool(RESULTS["seed"]), r_signup[:80])
    chk("B6 signup auto-login announced", "logged in here right away" in r_signup)
    p_sign = PROBES.get("after-signup", {})
    chk("B6 exactly ONE signup reached the hub (one-shot works)", p_sign.get("n_acc") == 2,
        f"n_acc={p_sign.get('n_acc')} (1 regression + 1 test signup)")
    chk("B6 wrapper session alive after signup", p_sign.get("mine") == "present",
        f"{p_sign.get('mine')} acct={p_sign.get('mine_acct')}")
    chk("B6 one-prompt listing created via chat", RESULTS["listing_id"] is not None, r_list[:80])
    chk("B6 login-seed welcome from SECOND sender", "Welcome back" in r_login and RESULTS["account_id"], r_login[:80])
    chk("B6 codeless edit accepted via account", f"Updated {RESULTS['listing_id']}" in r_edit and "price" in r_edit, r_edit[:80])

    # C1: chat parity — whoami / archive / unarchive through the real wrapper
    r_who = RESULTS["whoami"] or ""
    r_arch = RESULTS["archive"] or ""
    r_unarch = RESULTS["unarchive"] or ""
    chk("C1 whoami shows account + verification provenance + payout guidance",
        RESULTS["account_id"] in r_who and "not human-verified yet" in r_who
        and "set-payout" in r_who, r_who[:100])  # M14: provenance wording
    chk("C1 archive via chat (account-owned, no code)",
        "archived" in r_arch and "hidden from search" in r_arch, r_arch[:80])
    chk("C1 disk: listing archived after chat archive",
        PROBES.get("after-archive", {}).get("archived") is True, str(PROBES.get("after-archive")))
    chk("C1 unarchive via chat", "visible again" in r_unarch, r_unarch[:80])
    chk("C1 disk: listing visible again after chat unarchive",
        PROBES.get("after-unarchive", {}).get("archived") is False, str(PROBES.get("after-unarchive")))

    ok_state = False
    detail = "state unreadable"
    try:
        snap = json.loads(open(STATE).read())
        lst = next(x for x in snap["listings"] if x["id"] == RESULTS["listing_id"])
        ok_state = (lst.get("owner") == RESULTS["account_id"]
                    and abs(float(lst.get("price", 0)) - 7.0) < 1e-9)
        detail = f"owner={str(lst.get('owner'))[:28]} price={lst.get('price')}"
    except Exception as ex:
        detail = f"state error: {ex}"
    chk("B6 state.json: listing owned by account, price edited", ok_state, detail)

    print("\n=== FULL REPLIES ===")
    for k in ("signup", "list", "login_seed", "edit", "whoami", "archive", "unarchive"):
        print(f"--- {k}:\n{RESULTS[k]}")
    print("=== SESSION KEYS:", [k[:20] + "..." for k in getattr(w.chatlib, "_SESSIONS", {})])
    sys.stdout.flush()

    fails = [n for n, ok in checks if not ok]
    print(f"\n=== chat-e2e: {len(checks) - len(fails)}/{len(checks)} passed ===")
    if fails:
        print("FAILED:", fails)
        sys.stdout.flush()
        os._exit(1)
    print(f"CHAT_E2E_PASSED: listing={RESULTS['listing_id']} account={RESULTS['account_id']}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except SystemExit:
        raise
