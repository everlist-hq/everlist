"""Chat intent layer for the EverList Booking chat agent (B3b/B3c).

Deterministic, LLM-free intent mapping: chat text -> hub query -> reply text.
Kept separate from wrapper.py so it is unit-tested without starting an Agent.

Supported intents:
  search/find/listings [query] -> GET /search?q=...
  list Title | cat | date | price | loc | cap -> POST /listings (one-prompt listing, B3c)
  book <id> <name>             -> books FREE listings for logged-in accounts; guidance otherwise
  fee/commission               -> manifest declared-fee transparency info
  help/hello/anything else     -> capability summary (fallback treats text as search)
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import serialization as _ser
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _EdPriv

# B3c: per-sender anti-spam cap for chat-created listings (in-memory, pilot-grade)
_LIST_CAP = 3
_list_counts: dict[str, int] = {}


def _hub_get(hub_url: str, path: str, token: str | None = None) -> dict:
    req = urllib.request.Request(hub_url.rstrip("/") + path)
    if token:
        req.add_header("X-Hub-Token", token)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode())


def _hub_post(hub_url: str, path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        hub_url.rstrip("/") + path, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Hub-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        # server said no (validation/auth): surface the real reason, never 'unreachable'
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": f"hub rejected the request (HTTP {e.code})"}


def _hub_delete(hub_url: str, path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    """H7: DELETE with body (token-authed), surfacing real rejection reasons."""
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        hub_url.rstrip("/") + path, data=body, method="DELETE",
        headers={"Content-Type": "application/json"},
    )
    if token:
        req.add_header("X-Hub-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {"error": f"hub rejected the request (HTTP {e.code})"}


def _show_listing(hub_url: str, arg: str) -> str:
    """H9: full listing detail via GET /listings/{id} (404 unknown, 410 archived)."""
    lid = (arg or "").strip()
    if not lid or " " in lid or "/" in lid:
        return "Usage: show <listing id> — e.g. 'show even-3'"
    try:
        l = _hub_get(hub_url, f"/listings/{lid}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return f"No listing '{lid}'. Try 'search' to browse, or check the id."
        if e.code == 410:
            return f"Listing '{lid}' is archived — its owner hid it."
        return f"Could not fetch listing (HTTP {e.code})."
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    extra = ""
    if l.get("capacity"):
        extra = f"\nBooked: {l.get('registered', 0)}/{l['capacity']}"
    return _fmt_listing(l) + extra


def _booking_status(hub_url: str, sender: str, arg: str) -> str:
    """H10: poll one booking's status. Logged-in: session token. Anonymous:
    re-mint /access for this chat's own agent address (deterministic principal
    — the same identity that made the booking). No existence oracle: unknown
    or not-yours are the same answer."""
    bid = (arg or "").strip()
    if not bid or " " in bid or "/" in bid:
        return "Usage: booking <booking id> — e.g. 'booking bk-abc123'"
    s = _session(sender)
    token = (s or {}).get("tokens", {}).get("book") or (s or {}).get("tokens", {}).get("list")
    note = ""
    if not token:
        # anonymous chat: this chat's agent address IS its principal — re-mint
        try:
            acc = _hub_post(hub_url, "/access", {"agent": sender, "acts": ["book"]})
            token = acc[1].get("tokens", {}).get("book")
            note = "(acting as this chat's agent identity) "
        except Exception:
            return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    try:
        b = _hub_get(hub_url, f"/bookings/{bid}", token=token)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return (f"No booking '{bid}' is visible to you — it either doesn't exist "
                    "or you are not its buyer/owner.")
        return f"Could not fetch booking (HTTP {e.code})."
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    qty = b.get("quantity", 1)
    qty_s = f" x{qty}" if qty > 1 else ""
    return (f"📋 Booking {b['id']}{note}\n"
            f"Listing: {b.get('listing_id')} | Status: {b.get('escrow', '?')} | "
            f"Amount: {b.get('amount', '?')} USD (fee {b.get('hub_fee', 0)}, payout {b.get('owner_payout', 0)}){qty_s}\n"
            f"Private details (name etc.) stay secret-gated: 'book {bid}' shows how they're retrieved.")


def _fmt_listing(l: dict) -> str:
    price = l.get("price", "?")
    avail = "spots open" if l.get("available") else "SOLD OUT"
    cat = l.get("category") or "listing"
    date = l.get("date") or ""
    when = f" on {date}" if date else ""
    line = f"• {l.get('title', '?')} [{cat}]{when} — {price} USD, {avail} (id: {l.get('id')})"
    extra = []
    if l.get("description"):
        d = str(l["description"]).strip()
        extra.append("  " + (d[:100] + "…" if len(d) > 100 else d))
    if l.get("url"):
        extra.append(f"  🔗 {l['url']}")
    pt = l.get("payment_terms")  # C12: terms are part of the public listing face
    if isinstance(pt, dict):
        if pt.get("rail") == "instant":
            extra.append("  ⚡ instant rail — settled at booking, no refund window")
        else:
            _dep = f" · deposit {pt.get('deposit_required')}" if pt.get("deposit_required") else ""
            extra.append(f"  🛡 escrow · refund window {pt.get('refund_window_hours', '?')}h{_dep}")
    return line + ("\n" + "\n".join(extra) if extra else "")


_RICH_KEYS = ("title", "category", "date", "price", "location", "capacity",
              "description", "tags", "url", "merchant", "vertical", "provider",
              "duration_minutes")


def _parse_rich(body: str) -> dict | None:
    """Parse multi-line `key: value` format; None if body isn't rich format."""
    if "\n" not in body:
        return None
    fields: dict[str, str] = {}
    for ln in body.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        if ":" in ln:
            k, _, v = ln.partition(":")
            k = k.strip().lower()
            if k in _RICH_KEYS or re.fullmatch(r"[a-z_]{1,24}", k):  # C5: community verticals add their own keys
                fields[k] = v.strip()
                continue
        if "title" not in fields and "|" not in ln:
            fields["title"] = ln  # bare first line = title
    return fields


# B3c-accounts: per-sender chat sessions (sender -> {account_id, tokens, verified, ts}).
# In-memory, pilot-grade: a logged-in chat acts AS the account for 24h (hub TTL).
_SESSIONS = {}
_SESSIONS_CAP = 10_000  # HARDENING: bound memory vs unique-sender spam (evict oldest)
_SESSION_TTL = 24 * 3600
_ACCOUNT_CAP = 25   # verified organizers get a higher listing cap than anonymous chat


def _session(sender: str):
    """Return the session dict for this sender, or None if expired/absent."""
    if len(_SESSIONS) >= _SESSIONS_CAP and sender not in _SESSIONS:
        for k in sorted(_SESSIONS, key=lambda k: _SESSIONS[k].get("ts", 0))[:len(_SESSIONS) // 10]:
            _SESSIONS.pop(k, None)  # evict oldest 10%
    s = _SESSIONS.get(sender)
    if not s or time.time() - s["ts"] > _SESSION_TTL:
        _SESSIONS.pop(sender, None)
        return None
    return s



def _pow_solve(hub_url: str, kind: str):
    """HARDENING-v2: fetch a PoW challenge and burn the required CPU here.
    Returns {challenge, nonce} or None if the hub is unreachable."""
    try:
        ch = _hub_get(hub_url, f"/auth/challenge?kind={kind}")
    except Exception:
        return None
    challenge, diff = ch["challenge"], int(ch["difficulty"])
    n = 0
    while True:
        d = hashlib.sha256((challenge + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0:
                bits += 8
                continue
            bits += 8 - b.bit_length()  # leading zero bits of first nonzero byte
            break
        if bits >= diff:
            return {"challenge": challenge, "nonce": n}
        n += 1


def _pubkey_of(seed_hex: str) -> str:
    """ed25519 public key (64 hex) for a 32-byte seed; '' if seed invalid."""
    try:
        sk = _EdPriv.from_private_bytes(bytes.fromhex(seed_hex))
    except Exception:
        return ""
    return sk.public_key().public_bytes(_ser.Encoding.Raw, _ser.PublicFormat.Raw).hex()


def _sign_login(hub_url: str, seed_hex: str, agent: str):
    """Challenge-response login: server proves we need no stored secret.
    Returns (status_code, body_dict)."""
    pub = _pubkey_of(seed_hex)
    if not pub:
        return None, {"error": "invalid seed (64 hex chars expected)"}
    try:
        ch = _hub_get(hub_url, f"/auth/challenge?kind=login&pubkey={pub}")
        sk = _EdPriv.from_private_bytes(bytes.fromhex(seed_hex))
        sig = sk.sign(b"everlist-login:" + ch["challenge"].encode()).hex()
        return _hub_post(hub_url, "/accounts/login", {"pubkey": pub, "agent": agent, "sig": sig})
    except urllib.error.HTTPError as ex:
        # real hub rejection (unknown pubkey, bad sig): surface the true reason,
        # never mask it as 'unreachable'
        try:
            return ex.code, json.loads(ex.read().decode())
        except Exception:
            return ex.code, {"error": f"HTTP {ex.code}"}
    except Exception:
        return None, {"unreachable": True}


def _set_session(sender: str, res: dict) -> None:
    _SESSIONS[sender] = {"account_id": res.get("account_id"), "tokens": res.get("tokens", {}),
                         "verified": bool(res.get("human_verified")), "ts": time.time(),
                         "payout_pk": res.get("payout_pk"),
                         "verified_by": res.get("verified_by"),
                         "midnight_credential": res.get("midnight_credential")}

def _welcome(res: dict) -> str:
    v = ("\n✅ You are verified as human — bookings need no extra credential."
         if res.get("human_verified") else
         "\n⏳ Not yet human-verified — ask the hub operator to vouch for you (pilot).")
    return (f"✅ Welcome back! This chat now acts as account {res.get('account_id')} (24h). "
            f"Your listings: cap {_ACCOUNT_CAP}, no per-listing codes needed.{v}")


def _signup(hub_url: str, sender: str) -> str:
    """CRYPTO accounts: the seed is generated HERE and shown ONCE; the hub
    stores ONLY the public key. Nothing secret ever exists server-side."""
    pow_ = _pow_solve(hub_url, "signup")
    if pow_ is None:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    seed = os.urandom(32).hex()
    pub = _pubkey_of(seed)
    try:
        code, res = _hub_post(hub_url, "/accounts/signup", {"agent": sender, "pubkey": pub, "pow": pow_})
    except urllib.error.HTTPError as ex:
        try:
            err = json.loads(ex.read().decode()).get("error", "")
        except Exception:
            err = ""
        return f"Signup rejected: {err or ('HTTP ' + str(ex.code))}"
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if code != 201:
        return f"Signup rejected: {res.get('error') or ('HTTP ' + str(code))}"
    aid = res.get("account_id")
    code2, lres = _sign_login(hub_url, seed, sender)  # auto-login: we still hold the seed
    if code2 == 200:
        _set_session(sender, lres)
        logged = "\n✅ You are logged in here right away — list away!"
    else:
        logged = "\nLog in here with: login-seed <seed>"
    return (f"✅ Account created ({aid}) — cryptographic kind.\n\n"
            f"🔑 Your account SEED (shown ONCE — store it like a crypto seed phrase):\n"
            f"{seed}\n"
            "The hub stores ONLY your public key — it cannot leak or lose your secret. "
            "From any other chat: login-seed <seed>"
            + logged + "\n"
            "Next: 'email-bind you@example.com' enables self-service recovery, and "
            "operator vouch makes you human-verified (pilot).")



def _login(hub_url: str, sender: str, code: str) -> str:
    """Legacy code-account login (pre-crypto accounts + rotate/recover codes)."""
    if not code:
        return "Usage: login acct-xxxxxxxx  (your account code) — or login-seed <seed> for keypair accounts"
    try:
        code_st, res = _hub_post(hub_url, "/accounts/login", {"account_code": code.strip(), "agent": sender})
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if code_st != 200:
        # _hub_post RETURNS (status, body) for HTTP rejections. A failed login
        # must never mint a session (B10: a bogus code used to answer
        # 'Welcome back ... account None' and poison the chat session).
        return f"❌ {res.get('error') or 'Invalid account code. Check it and try again.'}"
    _set_session(sender, res)
    return _welcome(res)


def _login_seed(hub_url: str, sender: str, seed: str) -> str:
    """Keypair-account login: challenge-response, the seed itself is never sent."""
    seed = seed.strip().lower().removeprefix("elseed-")
    if not re.fullmatch(r"[0-9a-f]{64}", seed or ""):
        return "Usage: login-seed <64-hex seed from signup>"
    code2, res = _sign_login(hub_url, seed, sender)
    if code2 is None:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"❌ {res.get('error', 'Login failed — check the seed.')}"
    _set_session(sender, res)
    return _welcome(res)



def _email_bind(hub_url: str, sender: str, email: str) -> str:
    s = _session(sender)
    if not s:
        return "Login first (login <code>) - then 'email-bind me@example.com' attaches a recovery email."
    try:
        code2, res = _hub_post(hub_url, "/accounts/email/bind", {"email": email}, token=s["tokens"].get("list"))
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"Email bind rejected: {res.get('error', 'unknown reason')}"
    s["pending_email"] = res.get("email")  # remember for email-code confirmation
    mode = res.get("delivery", "")
    note = ("(dev mode: code visible in hub log)" if mode == "logged" else
            "check your inbox" if mode == "sent" else f"delivery mode: {mode}")
    return (f"Verification code sent to {res.get('email')} - {note}. "
            "Confirm with: email-code <6-char code>")


def _email_code(hub_url: str, sender: str, code: str) -> str:
    s = _session(sender)
    if not s:
        return "Login first, then confirm your email code."
    try:
        pending = s.get("pending_email")
        if not pending:
            return "No email pending in this chat. First: email-bind <your email>"
        code2, res = _hub_post(hub_url, "/accounts/email/verify", {"email": pending, "code": code})
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"{res.get('error', 'Invalid or expired code. Request a new one with email-bind <email>.')}"
    if res.get("account_id") != s.get("account_id"):
        return "That code verified a different chat's pending email. Do the bind from this chat."
    return ("Email verified - recovery enabled! If you ever lose your account code:\n"
            "  recover <your email>  -> code arrives by email\n"
            "  recover-confirm <code>  -> new account code (old one dies)")


def _recover(hub_url: str, email: str) -> str:
    if not email:
        return "Usage: recover you@example.com"
    try:
        pow_ = _pow_solve(hub_url, "recover")
        if pow_ is None:
            raise RuntimeError
        _, res = _hub_post(hub_url, "/accounts/email/recover", {"email": email, "pow": pow_})
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    mode = res.get("delivery", "")
    note = ("(dev mode: code visible in hub log)" if mode == "logged" else
            "check your inbox" if mode == "sent" else
            "a recovery code was sent if that email is bound to an account")
    return f"{note}. Then: recover-confirm <email> <code>"


def _recover_confirm(hub_url: str, email: str, code: str) -> str:
    """Recovery rotates the credential: keypair accounts get a fresh seed (shown
    ONCE, hub stores only the new pubkey); legacy code accounts get a fresh code."""
    if not email or not code:
        return "Usage: recover-confirm <email> <6-char code from the recovery email>"
    seed = os.urandom(32).hex()
    pub = _pubkey_of(seed)
    try:
        code2, res = _hub_post(hub_url, "/accounts/email/recover/confirm",
                               {"email": email, "code": code, "pubkey": pub})
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"{res.get('error', 'Invalid or expired recovery code. Request a new one with recover <email>.')}"
    if res.get("kind") == "keypair":
        return (f"Recovered account {res.get('account_id')}!\n\n"
                f"\U0001f511 Your NEW account SEED (shown ONCE): {seed}\n"
                "Store it - and 'login-seed <seed>' to continue here.")
    return (f"Recovered account {res.get('account_id')}!\n\n"
            f"Your NEW account code (shown ONCE): {res.get('account_code')}\n"
            "Store it - and 'login <code>' to continue here.")



def _whoami(hub_url: str, sender: str) -> str:
    s = _session(sender)
    if not s:
        return ("You're chatting anonymously (per-listing codes, cap 3). "
                "'signup' creates an account; 'login-seed <seed>' or 'login <code>' restores yours.")
    payout = s.get("payout_pk")
    pline = ("payout key set ✅" if payout else
             "no payout key yet ('set-payout <64-hex coin PUBLIC key>' — escrow payouts target it)")
    vby = s.get("verified_by")
    vline = {"midnight-zk": "✅ verified: Midnight ZK credential (Tier-2)",
             "midnight-zk-revoked": "⚠️ your Midnight credential was REVOKED — verification lost",
             "admin-vouch": "✅ verified: operator vouch (pilot)"}.get(
        vby, "⏳ not human-verified yet ('verify-midnight <credential_id>' or ask the operator)")
    return (f"Logged in as {s['account_id']} · {vline} · "
            f"listing cap {_ACCOUNT_CAP} · {pline}. 'logout' to end the session here.")


def _verify_midnight(hub_url: str, sender: str, cid: str) -> str:
    """M14 Tier-2 sign-in: present a Midnight credential id; the hub checks the
    credential contract (admitted + not revoked) and marks the account
    verified_by: midnight-zk server-side."""
    s = _session(sender)
    if not s:
        return "Login first ('login <code>' / 'login-seed <seed>'), then verify-midnight."
    cid = (cid or "").strip()
    if not re.fullmatch(r"[1-9][0-9]{0,11}", cid):
        return ("Usage: verify-midnight <credential_id>\n"
                "The credential id comes from your wallet's Midnight personhood registration (M15 walkthrough).")
    code, res = _hub_post(hub_url, "/accounts/verify-midnight",
                          {"credential_id": cid}, s["tokens"].get("list"))
    if code is None:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if code == 200:
        s["verified"] = True
        s["verified_by"] = res.get("verified_by")
        s["midnight_credential"] = int(cid)
        return (f"✅ Midnight credential {cid} verified ({res.get('mode')} mode, tx …{str(res.get('evidence_tx'))[-8:]})\n"
                "You are now human-verified via Tier-2 — bookings need no extra credential.")
    if code == 409:
        return f"❌ {res.get('error', 'credential binding conflict')}"
    if code == 502:
        return f"⚠️ {res.get('error', 'verifier unavailable')} ({res.get('mode', '?')} mode) — fail-closed, nothing changed."
    if res.get("revoked"):
        s["verified"] = False
        s["verified_by"] = "midnight-zk-revoked"
        return f"❌ credential {cid} is REVOKED on-chain — verification lost (fail-closed)."
    return f"❌ {res.get('error', 'credential not verified')} ({res.get('mode', '?')} mode)"


def _set_payout(hub_url: str, sender: str, pk: str) -> str:
    s = _session(sender)
    if not s:
        return "Login first ('login <code>' / 'login-seed <seed>'), then set-payout."
    pk = pk.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", pk or ""):
        return ("Usage: set-payout <64-hex coin PUBLIC key>\n"
                "⚠️ PUBLIC key only — never send a secret key or seed; the hub stores public keys and never needs secrets.")
    code, res = _hub_post(hub_url, "/accounts/payout", {"payout_pk": pk}, s["tokens"].get("list"))
    if code is None:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if code != 200:
        return f"❌ {res.get('error', 'payout registration failed')}"
    s["payout_pk"] = pk
    return (f"✅ Payout key registered ({pk[:12]}…). Midnight escrow releases target this coin key.\n"
            "Keep the matching secret ONLY in your wallet — no one else will ever need it.")


def _logout(sender: str) -> str:
    if _SESSIONS.pop(sender, None):
        return "👋 Logged out. This chat is anonymous again."
    return "You weren't logged in."


def _logout_all(hub_url: str, sender: str) -> str:
    """B1: revoke every login token of this account (all chats/devices/agents)."""
    s = _session(sender)
    if not s:
        return ("Login first (login <code> or login-seed <seed>) - "
                "logout-all revokes every login of your account everywhere.")
    try:
        code2, res = _hub_post(hub_url, "/accounts/logout-all", {}, token=s["tokens"].get("list"))
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"{res.get('error', 'Could not revoke sessions.')}"
    _SESSIONS.pop(sender, None)
    return ("🔒 Every login token of your account is revoked - on every chat, device and agent. "
            "Log in again wherever you still need access.")


def _delete_account(hub_url: str, sender: str, arg: str) -> str:
    """H7: two-step account self-deletion. Step 1 asks for the typed account id
    (intent proof against accidents); step 2 performs it and ends the session.
    The public ledger keeps its pseudonymous refs — the money trail survives."""
    s = _session(sender)
    if not s:
        return ("Login first (login <code> or login-seed <seed>) - "
                "delete-account erases YOUR logged-in account and archives its listings.")
    arg = (arg or "").strip()
    if not arg:
        s["pending_delete"] = s.get("account_id")
        aid = s.get("account_id", "?")
        return (f"⚠️ This PERMANENTLY erases account {aid}: your recovery email is deleted and "
                f"all your listings are archived (bookers keep their escrow rights).\n"
                f"Sure? Type:  delete-account confirm {aid}")
    if not s.get("pending_delete"):
        return "Safety first: run delete-account once to see what it does, then confirm."
    expected = s.get("pending_delete")
    if arg != f"confirm {expected}" and arg != expected:
        return (f"Confirmation mismatch. To erase account {expected}, type exactly:\n"
                f"delete-account confirm {expected}")
    try:
        code2, res = _hub_delete(hub_url, "/accounts/me", {"confirm": expected},
                                 token=s["tokens"].get("list"))
    except Exception:
        return "Sorry - the EverList hub is unreachable right now. Try again shortly."
    if code2 != 200:
        return f"{res.get('error', 'Could not delete the account.')}"
    _SESSIONS.pop(sender, None)
    n = res.get("listings_archived", 0)
    return (f"🗑️ Account {expected} is erased (recovery email deleted, every token revoked). "
            f"{n} listing(s) archived. The public ledger keeps its pseudonymous refs for escrow auditability.")


def _create_listing(hub_url: str, sender: str, text: str) -> str:
    """One-prompt listing, two formats:
    Quick:  list Title | category | date | price | location | capacity
    Rich:   list\n title: ... \n description: ... \n tags: a, b \n url: https://...
    Only title and price are mandatory; the rest get honest defaults (events vertical)."""
    sender = (sender or "anonymous-chat")[:128]
    sess = _session(sender)
    cap = _ACCOUNT_CAP if sess else _LIST_CAP
    if _list_counts.get(sender, 0) >= cap:
        return (f"You've reached the pilot limit of {cap} listings "
                + ("for this account." if sess else "per agent. "
                   "'signup' creates an account with cap 25.") )
    body = text.strip()[4:].strip()
    rich = _parse_rich(body)
    if rich is not None:
        parts = [rich.get("title", "")]
        tail = [rich.get(k, "") for k in ("category", "date", "price", "location", "capacity")]
        parts += tail
        extra = {k: rich[k] for k in ("description", "tags", "url", "vertical",
                                      "provider", "duration_minutes") if rich.get(k)}
    else:
        parts = [p.strip() for p in body.split("|")]
        extra = {}
    if not parts or not parts[0]:
        return ("To list an event, send either:\n"
                "list Rooftop Jazz Night | concert | 2026-09-20 | 15 | Berlin | 50\n"
                "or rich format:\n"
                "list\ntitle: Free Surya Kriya Taster Class\ncategory: workshop\n"
                "date: 2026-09-20\nprice: 0\nlocation: Bad Tatzmannsdorf\ncapacity: 12\n"
                "description: what your class is about\ntags: yoga, free\n"
                "url: https://your-site.example\n"
                "(only title and price are required)")
    title = parts[0][:80]
    category = (parts[1].lower() if len(parts) > 1 and parts[1] else "other")
    date = parts[2] if len(parts) > 2 and parts[2] else "TBD"
    price = parts[3] if len(parts) > 3 and parts[3] else "0"
    location = parts[4] if len(parts) > 4 and parts[4] else "TBD"
    capacity = parts[5] if len(parts) > 5 and parts[5] else "20"
    try:
        float(price)
    except ValueError:
        return (f"Price must be a number, got {price!r}. "
                "Example: list Rooftop Jazz Night | concert | 2026-09-20 | 15 | Berlin | 50")
    try:
        cap = int(capacity)
    except ValueError:
        cap = 20
    # mint a list token for this sender, then create the listing (owner = token principal)
    # H15: vertical is DATA - chat supports events (default) and services; the
    # hub schema (GET /verticals) decides required fields, not chat code.
    vert = str(extra.get("vertical", "events")).strip().lower()
    if vert not in ("events", "services", "classes"):
        # C5: community verticals - ask the hub's live registry instead of hardcoding
        try:
            _known = sorted(_hub_get(hub_url, "/verticals")["verticals"].keys())
            _extra = [v for v in _known if v not in ("events", "food", "services")]  # built-ins only; food has no chat branch yet
        except Exception:
            _extra = []
        _hint = (", plus community verticals: " + ", ".join(_extra)) if _extra else ","
        return (f"Unknown vertical '{vert}'. Chat supports: events (default), services{_hint}\n"
                "Example:\nlist\nvertical: services\ntitle: Mobile Massage\n"
                "provider: Serenity Spa\nprice: 30\ncategory: wellness")
    if vert == "classes":
        # C5 reference community vertical - payload per ITS hub schema (live)
        try:
            sch = _hub_get(hub_url, "/verticals")["verticals"]["classes"]
        except Exception:
            return "Sorry - the EverList hub is unreachable right now. Try again shortly."
        payload = {"vertical": "classes", "title": title, "price": float(price),
                   "date": date, "location": location, "source": "chat-agent"}
        if category:
            payload["category"] = category
        payload["capacity"] = cap  # classes tracks capacity like events
        for k in ("instructor", "skill_level", "duration_minutes"):
            v = str(extra.get(k, "")).strip()
            if v:
                try:
                    payload[k] = int(v)  # positive_int fields; hub re-validates
                except ValueError:
                    payload[k] = v
        missing = [f for f in sch["required"] if f not in payload]
        if missing:
            return (f"Classes listings need: {', '.join(missing)}. Example:\n"
                    "list\nvertical: classes\ntitle: Morning Vinyasa\nprice: 12\n"
                    "date: 2026-09-25\nlocation: Vienna\ncapacity: 12\n"
                    "instructor: Ana\nskill_level: beginner\ncategory: yoga")
    elif vert == "services":  # C5: chained - classes branch above already built its payload
        provider = str(extra.get("provider", "")).strip()
        if not provider:
            return ("Services listings need a provider. Example:\n"
                    "list\nvertical: services\ntitle: Mobile Massage\nprovider: Serenity Spa\n"
                    "price: 30\ncategory: wellness\nlocation: Vienna\nduration_minutes: 60\n"
                    "description: what you offer")
        payload = {
            "vertical": "services", "title": title, "provider": provider,
            "price": float(price), "category": category, "source": "chat-agent",
        }
        if location and location != "TBD":
            payload["location"] = location
        try:
            payload["duration_minutes"] = int(str(extra.get("duration_minutes", "")).strip())
        except (TypeError, ValueError):
            pass
    elif vert == "events":
        payload = {
            "vertical": "events", "title": title, "category": category,
            "date": date, "price": float(price), "location": location,
            "capacity": cap, "source": "chat-agent",
        }
    else:  # unreachable while the whitelist gate above holds - stay honest if it ever drifts
        return "Internal routing error: no listing builder for this vertical. Please report it."
    if extra.get("description"):
        payload["description"] = extra["description"][:500]
    if extra.get("url"):
        payload["url"] = extra["url"][:300]
    if extra.get("tags"):
        payload["tags"] = extra["tags"]
    # C12: chat merchants can set payment terms in rich format
    # (rail: escrow|instant, refund_window: hours, deposit: amount)
    if any(extra.get(k) for k in ("rail", "refund_window", "deposit")):
        _rail = str(extra.get("rail") or "escrow").strip().lower()
        _ptc = {"rail": _rail}
        if extra.get("refund_window"):
            try:
                _ptc["refund_window_hours"] = int(str(extra["refund_window"]).strip())
            except ValueError:
                return "refund_window must be a whole number of hours (1-720), e.g. 'refund_window: 72'"
        if extra.get("deposit"):
            try:
                _ptc["deposit_required"] = float(str(extra["deposit"]).strip())
            except ValueError:
                return "deposit must be a number, e.g. 'deposit: 5'"
        payload["payment_terms"] = _ptc
    try:
        if sess:
            token = sess["tokens"]["list"]   # sub=acct-<id>: listing owned by the ACCOUNT
        else:
            _, acc = _hub_post(hub_url, "/access", {"agent": sender, "acts": ["list"]})
            token = acc["tokens"]["list"]
        status, res = _hub_post(hub_url, "/listings", payload, token=token)
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if status != 201:
        return f"Listing rejected: {res.get('error') or res.get('known') or 'unknown error'}"
    _list_counts[sender] = _list_counts.get(sender, 0) + 1
    if sess:
        return (f"✅ Listed! '{res.get('title', title)}' is live (id: {res.get('id')}). "
                f"Anyone can find it with 'search'. "
                f"({_list_counts[sender]}/{_ACCOUNT_CAP} listings used)\n"
                f"Owned by your account — edit/delete anytime, no code needed: "
                f"'edit {res.get('id')} price: 5' or 'delete {res.get('id')}'")
    code = res.get("manage_code", "")
    return (f"✅ Listed! '{res.get('title', title)}' is live (id: {res.get('id')}). "
            f"Anyone can find it with 'search'. "
            f"({_list_counts[sender]}/{_LIST_CAP} listings used)\n\n"
            f"🔑 Your manage code (shown ONCE — store it!): {code}\n"
            f"It owns the listing: 'edit {res.get('id')} <code> ...' or 'delete {res.get('id')} <code>'. "
            f"Tip: 'signup' gives you an account (cap 25, no codes).")


def _owned_listing(hub_url: str, sender: str, body: str, action: str) -> str:
    """Edit/delete with two credential modes:
    logged-in account:  edit <id> field: value [...]        (session token IS the proof)
    anonymous chat:     edit <id> <manage_code> field: value [...]"""
    sess = _session(sender)
    bits = body.split(None, 2)  # [id, code?, rest?] / [id, rest?] when logged in
    lid = bits[0].strip() if bits else ""
    if not lid:
        return (f"Usage: {action} <listing_id>"
                + ("" if sess else " <manage_code>")
                + (" field: value [...] (e.g. price: 5, capacity: 30)" if action == "edit" else ""))
    payload = {"action": action}
    code = ""
    if sess:
        rest = body[len(lid):].strip()          # everything after id = edit fields
    else:
        code = bits[1].strip() if len(bits) > 1 else ""
        rest = bits[2] if len(bits) > 2 else ""
        payload["manage_code"] = code
    if action == "edit":
        rich = _parse_rich(rest) or {}
        # also accept single-line 'field: value' pairs (e.g. 'edit ev-12 price: 5 capacity: 30')
        if not rich:
            for m in re.finditer(r"(title|description|price|location|date|capacity|category|tags|url)\s*:\s*([^:]+)(?=\s+\w+\s*:|$)", rest):
                rich[m.group(1)] = m.group(2).strip()
        for k in ("title", "description", "price", "location", "date", "capacity", "category", "tags", "url"):
            if rich.get(k):
                payload[k] = rich[k]
        if len(payload) == (2 if not sess else 1):
            return ("Nothing to change. Example: "
                    + (f"edit {lid} price: 5 capacity: 30" if sess else f"edit {lid} mgr-abc123 price: 5"))
    try:
        if sess:
            token = (sess.get("tokens") or {}).get("list")  # sub=acct-<id>: hub checks account ownership
            if not token:
                return "Session expired — 'login <account_code>' again, or use your manage code."
        else:
            _, acc = _hub_post(hub_url, "/access", {"agent": sender, "acts": ["list"]})
            token = acc["tokens"]["list"]
        status, res = _hub_post(hub_url, f"/listings/{urllib.parse.quote(lid)}/manage", payload, token=token)
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if status == 401:
        return "Session expired — 'login <account_code>' again, or use your manage code."
    if status == 403:
        return ("❌ Not authorized: wrong manage code (shown once at creation), "
                "or the listing belongs to another account.")
    if status != 200:
        return f"Rejected: {res.get('error') or 'unknown error'}"
    if action == "delete":
        _list_counts[sender] = max(0, _list_counts.get(sender, 1) - 1)
        return f"🗑️ Listing {lid} deleted. Slot freed ({_list_counts[sender]}/{_ACCOUNT_CAP if sess else _LIST_CAP} used)."
    if action == "archive":
        return f"📁 Listing {lid} archived — hidden from search; existing bookings stay fulfillable. 'unarchive {lid}' restores it."
    if action == "unarchive":
        return f"📂 Listing {lid} unarchived — visible again."
    return f"✅ Updated {lid}: changed {', '.join(res.get('fields', []))}."


def _my_listings(hub_url: str, sender: str) -> str:
    sess = _session(sender)
    sender_n = (sender or "anonymous-chat")[:128]
    try:
        data = _hub_get(hub_url, "/listings")
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    mine = [l for l in (data.get("listings") or [])
            if str(l.get("owner", "")) == (sess["account_id"] if sess else sender_n)]
    if not mine:
        return "You have no listings yet. Create one with 'list ...'"
    lines = [f"• {l['title']} (id: {l['id']}, price {l.get('price')} USD)" for l in mine]
    tail = "\n\nEdit: 'edit <id>" + ("' (no code — account session)" if sess else " <code>'") \
           + " · Delete: 'delete <id>" + ("'" if sess else " <code>'")
    return (f"Your listings ({len(mine)}):\n" + "\n".join(lines) + tail)


def _rate_booking(hub_url: str, sender: str, arg: str) -> str:
    """C4: buyer rates a SETTLED booking 1-5, once. Logged-in: session token.
    Anonymous: re-mint /access for this chat's agent address (the same
    deterministic principal that made the booking)."""
    bits = (arg or "").split()
    if len(bits) != 2 or not bits[1].isdigit() or not (1 <= int(bits[1]) <= 5):
        return "Usage: rate <booking_id> <1-5> — e.g. 'rate bk-abc123 5' (possible after the organizer confirms, or on free bookings)"
    bid, val = bits[0], int(bits[1])
    s = _session(sender)
    token = (s or {}).get("tokens", {}).get("book") or (s or {}).get("tokens", {}).get("list")
    if not token:
        try:
            acc = _hub_post(hub_url, "/access", {"agent": sender, "acts": ["book"]})
            token = (acc[1] or {}).get("tokens", {}).get("book")
        except Exception:
            return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if not token:
        return "Could not authenticate you — try 'login <account_code>'."
    status, res = _hub_post(hub_url, f"/book/{urllib.parse.quote(bid)}/rate", {"rating": val}, token=token)
    if status == 200:
        agg = res.get("aggregate") or ""
        return f"⭐ Thanks! You rated {bid}: {val}/5." + (f" {agg}" if agg else "")
    if status == 401:
        return "Session expired — 'login <account_code>' again."
    if status == 403:
        return "❌ Only the booking's buyer can rate it."
    if status == 409:
        return f"Rejected: {res.get('error', 'not possible for this booking')}"
    if status == 404:
        return f"No booking '{bid}' is visible to you."
    if status == 400:
        return f"Rejected: {res.get('error', 'rating must be 1-5')}"
    return f"Could not rate (HTTP {status})."


def _my_bookings(hub_url: str, sender: str) -> str:
    """C3: principal-scoped booking list (hub GET /bookings). Logged-in:
    session token. Anonymous: re-mint /access for this chat's agent address
    (deterministic principal — same pattern as 'booking <id>')."""
    s = _session(sender)
    token = (s or {}).get("tokens", {}).get("book") or (s or {}).get("tokens", {}).get("list")
    note = ""
    if not token:
        try:
            acc = _hub_post(hub_url, "/access", {"agent": sender, "acts": ["book"]})
            token = (acc[1] or {}).get("tokens", {}).get("book")
            note = " (as this chat's agent identity)"
        except Exception:
            return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    if not token:
        return "Could not authenticate you — try 'login <account_code>'."
    try:
        data = _hub_get(hub_url, "/bookings", token=token)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "Session expired — 'login <account_code>' again."
        return f"Could not fetch bookings (HTTP {e.code})."
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    mine = data.get("bookings") or []
    if not mine:
        return ("You have no bookings yet. Browse with 'search' and book with "
                "'book <id> <name>' — free listings book instantly.")
    lines = []
    for b in mine[:10]:
        qty = b.get("quantity", 1)
        qty_s = f" x{qty}" if qty > 1 else ""
        lines.append(f"• {b.get('id')} — {b.get('listing_id')} | {b.get('escrow', '?')} | "
                     f"{b.get('amount', 0)} USD{qty_s}")
    more = f"\n(+{len(mine) - 10} more)" if len(mine) > 10 else ""
    return (f"Your bookings ({len(mine)}){note}:\n" + "\n".join(lines) + more
            + "\n\nPoll one: 'booking <id>' — shows escrow status.")


_FILLER_WORDS = {"find", "me", "a", "an", "the", "for", "please", "show", "us", "under", "over",
                 "something", "anything", "want", "looking", "i", "we", "to", "do",
                 "in", "on", "at", "my", "under", "around"}


def _smart_search(hub_url: str, text: str) -> str:
    """Natural-language search: 'find me a free yoga class' -> max_price=0 + word match.
    Multi-word queries union per-word matches (hub q is substring-AND by design)."""
    low = text.strip().lower()
    free = "free" in low.split()
    # C2 structured qualifiers, parsed OUT of the keyword text (regex spans so
    # dates/prices survive intact; qualifiers AND-combine in the hub):
    qual: dict[str, str] = {}

    def _take(pattern: str, key: str, group: int = 1) -> None:
        nonlocal low
        m = re.search(pattern, low)
        if m:
            qual[key] = m.group(group).replace(",", ".") if key.endswith("_price") else m.group(group)
            low = (low[:m.start()] + " " + low[m.end():]).strip()

    _take(r"\bunder\s+(\d+(?:[.,]\d+)?)", "max_price")
    _take(r"\bover\s+(\d+(?:[.,]\d+)?)", "min_price")
    _take(r"\bfrom\s+(\d{4}-\d{2}-\d{2})", "from")
    _take(r"\b(?:until|till|by)\s+(\d{4}-\d{2}-\d{2})", "to")
    if re.search(r"\bcheapest\b", low):
        qual["sort"] = "price"
        low = re.sub(r"\bcheapest\b", " ", low)
    if re.search(r"\b(soonest|earliest)\b", low):
        qual["sort"] = "date"
        low = re.sub(r"\b(?:soonest|earliest)\b", " ", low)
    params = ([] + (["max_price=0"] if free else []))
    for k in ("max_price", "min_price", "from", "to", "sort"):
        if k in qual:
            params.append(k + "=" + urllib.parse.quote(qual[k]))
    words = [w for w in low.replace(",", " ").split()
             if w not in _FILLER_WORDS and w != "free" and w not in ("search", "find", "listings", "events", "show")]
    try:
        data = _hub_get(hub_url, "/search" + (("?" + "&".join(params)) if params else ""))
        base = data.get("listings") or []
        if words:
            seen: dict[str, dict] = {}
            for w in words[:3]:
                d2 = _hub_get(hub_url, "/search?" + "&".join(params + ["q=" + urllib.parse.quote(w)]))
                for l in (d2.get("listings") or []):
                    seen.setdefault(l.get("id"), l)
            listings = list(seen.values())
        else:
            listings = base
    except urllib.error.HTTPError as ex:
        # B7 honesty: real hub rejections (e.g. q too long) must not masquerade
        # as 'unreachable' — surface the actual reason.
        try:
            err = json.loads(ex.read().decode()).get("error", "")
        except Exception:
            err = ""
        return f"Search rejected: {err or ('HTTP ' + str(ex.code))}"
    except Exception:
        return "Sorry — the EverList hub is unreachable right now. Try again shortly."
    parts = ([] + (["free only"] if free else []))
    if "max_price" in qual:
        parts.append("under " + qual["max_price"])
    if "min_price" in qual:
        parts.append("over " + qual["min_price"])
    if "from" in qual:
        parts.append("from " + qual["from"])
    if "to" in qual:
        parts.append("until " + qual["to"])
    if qual.get("sort") == "price":
        parts.append("cheapest first")
    if qual.get("sort") == "date":
        parts.append("soonest first")
    qualifier = (" (" + ", ".join(parts) + ")") if parts else ""
    if not listings:
        return (f"No listings matched{qualifier}. Try: 'search' (all), 'search jazz', "
                "or 'find me a free yoga class'.")
    lines = [_fmt_listing(l) for l in listings[:8]]
    tail = "\n\nTo book one, say 'book <id>' — free listings book without payment."
    return f"Found {len(listings)} listing(s){qualifier}:\n" + "\n".join(lines) + tail


_HELP = (
    "Hi! I'm EverList Booking — an open, escrow-protected marketplace "
    "where AI agents book real things.\n\nCommands:\n"
    "• search — all listings; 'search jazz' — filtered; 'find me a free yoga class' — natural language\n"
    "• filters: under/over <price> · from/until <YYYY-MM-DD> · soonest · cheapest (combine freely)\n"
    "• list <title> | <category> | <date> | <price> | <location> | <capacity> — publish in one message\n"
    "• list\n title: … description: … tags: … url: … — rich listing (description, tags, link)\n"
    "• signup — create a keypair organizer account (seed shown ONCE; cap 25, no per-listing codes)\n"
    "• login-seed <seed> — act as your keypair account from any chat (24h)\n"
    "• login <account_code> — legacy code accounts (24h)\n"
    "• email-bind <email> / email-code <code> — enable email recovery\n"
    "• recover <email> / recover-confirm <email> <code> — recover a lost account code\n"
    "• set-payout <64-hex coin PUBLIC key> — where Midnight escrow pays you (PUBLIC key only!)\n"
    "• verify-midnight <credential_id> — Tier-2 sign-in with your Midnight personhood credential\n"
    "• whoami — session status; logout — end session in this chat; logout-all — revoke every login\n"
    "• delete-account — erase your account (typed confirmation; listings archived, ledger refs kept)\n"
    "• my-listings — your listings\n"
    "• my-bookings — your bookings with escrow status (poll one: 'booking <id>')\n"
    "• edit <id> [code] price: 5 — change your listing (code only when anonymous)\n"
    "• delete <id> [code] — remove your listing\n"
    "• archive <id> [code] / unarchive <id> [code] — hide/restore a listing (registrations kept)\n"
    "• show <id> — full listing details (description, url, availability)\n"
    "• booking <id> — check your booking's escrow status (buyer or owner)\n"
    "• rate <booking_id> <1-5> — rate a settled booking (after confirm, or free listings)\n"
    "• book <id> <name> — book a FREE listing instantly with your account (paid = guidance)\n"
    "• fee — how our fee model stays fair"
)


def handle_text(hub_url: str, text: str, sender: str = "") -> str:
    """Map one incoming chat text to one reply text (pure function, testable)."""
    low = (text or "").strip().lower()

    # --- one-prompt listing creation (B3c) — before search ('list' vs 'listings')
    if low == "list" or low.startswith("list ") or low.startswith("list\n") or low.startswith("list\r\n"):
        return _create_listing(hub_url, sender, text.strip())

    # --- account auth (B3c-accounts)
    if low == "signup" or low.startswith("signup "):
        return _signup(hub_url, sender)
    if low.startswith("login-seed "):
        return _login_seed(hub_url, sender, text.strip()[11:].strip())
    if low.startswith("login"):
        return _login(hub_url, sender, text.strip()[5:].strip())
    if low in ("whoami", "account", "status"):
        return _whoami(hub_url, sender)
    if low == "logout":
        return _logout(sender)
    if low == "logout-all":
        return _logout_all(hub_url, sender)
    # H7: account deletion — must match BEFORE the 'delete ' listing branch
    if low == "delete-account" or low.startswith("delete-account "):
        return _delete_account(hub_url, sender, text.strip()[14:].strip())

    # --- email recovery (B3c-email)
    if low.startswith("email-bind "):
        return _email_bind(hub_url, sender, text.strip()[10:].strip())
    if low.startswith("email-code "):
        return _email_code(hub_url, sender, text.strip()[10:].strip())
    if low.startswith("recover-confirm "):
        bits = text.strip()[15:].strip().split(None, 1)
        email = bits[0] if bits else ""
        code = bits[1] if len(bits) > 1 else ""
        return _recover_confirm(hub_url, email, code)
    if low.startswith("recover "):
        return _recover(hub_url, text.strip()[8:].strip())

    # --- payout key (M9)
    if low.startswith("set-payout "):
        return _set_payout(hub_url, sender, text.strip()[11:].strip())
    if low == "set-payout":
        return _set_payout(hub_url, sender, "")

    # --- Midnight Tier-2 sign-in (M14)
    if low.startswith("verify-midnight "):
        return _verify_midnight(hub_url, sender, text.strip()[16:].strip())
    if low == "verify-midnight":
        return _verify_midnight(hub_url, sender, "")

    # --- ownership commands (B3c-ownership)
    if low == "my-bookings" or low == "my bookings":
        return _my_bookings(hub_url, sender)
    if low == "my-listings" or low == "my listings":
        return _my_listings(hub_url, sender)
    if low.startswith("edit ") or low.startswith("edit\n"):
        return _owned_listing(hub_url, sender, text.strip()[4:].strip(), "edit")
    if low.startswith("delete "):
        return _owned_listing(hub_url, sender, text.strip()[6:].strip(), "delete")
    # H9: full listing detail — BEFORE smart-search (it would eat 'show' as a search keyword)
    if low.startswith("show "):
        return _show_listing(hub_url, text.strip()[5:].strip())
    if low.startswith("unarchive "):
        return _owned_listing(hub_url, sender, text.strip()[9:].strip(), "unarchive")
    if low.startswith("archive "):
        return _owned_listing(hub_url, sender, text.strip()[8:].strip(), "archive")

    # --- smart natural-language search
    for kw in ("search", "find", "listings", "events", "show"):
        if low == kw or low.startswith(kw + " "):
            rest = text.strip()[len(kw):].strip()
            return _smart_search(hub_url, (kw + " " + rest) if rest else kw)

    # H10: booking status poll — BEFORE the booking-intent (startswith('book') would swallow it)
    if low.startswith("booking "):
        return _booking_status(hub_url, sender, text.strip()[8:].strip())
    # C4: buyer rates a settled booking (its own command — 'book' would swallow 'rate' otherwise never)
    if low == "rate" or low.startswith("rate "):
        return _rate_booking(hub_url, sender, text.strip()[4:].strip())

    # --- booking intent (H15: FREE listings book IN CHAT for logged-in accounts;
    # paid listings stay honest guidance - payment is a real gate)
    if low.startswith("book"):
        rest = text.strip()[4:].strip()
        bits = rest.split(None, 1)
        lid = bits[0] if bits else ""
        who = bits[1].strip() if len(bits) > 1 else ""
        target = None
        if lid:
            try:
                d = _hub_get(hub_url, "/search")
                target = next((l for l in (d.get("listings") or []) if l.get("id") == lid), None)
            except Exception:
                target = None
        if target is None:
            return (
                "Bookings need two things EverList enforces for fairness:\n"
                "1. a verified-human credential (fake agents are rejected)\n"
                "2. payment via x402 — skipped automatically for free listings (escrow WAIVED)\n"
                + (f"\nUse the EverList SDK (agenthub client) with listing id '{lid}'.\n" if lid else "\n")
                + "A booking agent with credentials can complete it end-to-end."
            )
        if float(target.get("price", 1)) > 0:
            pt = target.get("payment_terms")
            pt_line = ""
            if isinstance(pt, dict):
                pt_line = ("\n⚡ Terms: instant rail — settled at booking, no refund window."
                           if pt.get("rail") == "instant" else
                           f"\n🛡 Terms: escrow · refund window {pt.get('refund_window_hours', '?')}h"
                           + (f" · deposit {pt.get('deposit_required')}" if pt.get("deposit_required") else "")
                           + "\nCustom terms: the SDK booking must echo accepted_payment_terms exactly.")
            return (
                f"'{target.get('title')}' is a PAID listing ({target.get('price')}).\n"
                "Payment goes through x402 — use the EverList SDK (agenthub client) "
                f"with listing id '{lid}'. Free listings book right here in chat."
                + pt_line
            )
        sess = _session(sender)
        if not sess:
            return (f"'{target.get('title')}' is FREE — I can book it for you right here.\n"
                    "First create an account ('signup'), then: book " + lid + " <your-name>")
        if not who:
            return (f"'{target.get('title')}' is FREE. Who is the booking for?\n"
                    "book " + lid + " <your-name>")
        try:
            sch = _hub_get(hub_url, "/verticals")["verticals"][target["vertical"]]["booking"]
            payload = {"listing_id": lid, sch["identity"]: who[:80]}
            status, res = _hub_post(hub_url, "/book", payload, token=sess["tokens"]["book"])
        except Exception:
            return "Sorry — the EverList hub is unreachable right now. Try again shortly."
        if status == 201:
            return (f"✅ Booked! '{target.get('title')}' — booking {res.get('id')} "
                    f"(escrow {res.get('escrow')}, amount {res.get('amount')}).\n"
                    f"🔑 Booking secret (shown ONCE — view your private details with it): {res.get('booking_secret')}\n"
                    "The owner confirms via the hub; cancel before fulfillment via the SDK "
                    "(your cancel_token is in the booking).")
        err = res.get("error", "unknown error")
        if "verified-human" in err:
            return ("Booking rejected: your account needs a human proof.\n"
                    "Pilot: the operator can vouch for you. Production: Midnight zk-personhood "
                    "(real human, identity stays private).")
        return f"Booking rejected: {err}"

    # --- fee transparency intent
    if any(k in low for k in ("fee", "commission", "cost")):
        try:
            m = _hub_get(hub_url, "/.well-known/agent-hub.json")
            declared = m.get("payments", {}).get("hub_fee", m.get("fee"))
            return (
                "EverList hubs declare their fee openly in the manifest"
                + (f": {declared}." if declared is not None else ".")
                + " A public ledger records every transaction, and an independent"
                " conformance checker verifies declared vs actual. No hidden fees."
            )
        except Exception:
            return "The hub manifest is unreachable right now — try again shortly."

    # --- greeting/help
    if low in ("hi", "hello", "hey", "help", "menu", "commands"):
        return _HELP

    # --- fallback: treat the whole text as a search query
    return handle_text(hub_url, "search " + text.strip(), sender=sender)
