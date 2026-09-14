"""B10: chat help honesty (self-managed hub).
A help entry that lies is a trust bug: every command documented in _HELP must
parse and hit its OWN intent — never the search-as-fallback path.
Run: python test_chat_help.py
"""
import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-b10-")


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


logf = open(os.path.join(TMP, "hub.log"), "w")
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
assert wait_ready(PORT)
HUB = f"http://127.0.0.1:{PORT}"
atexit.register(lambda: (proc.terminate(), logf.close()))


def is_fallback(reply):
    """The fallback re-enters as search; with a reachable hub its signatures are
    '֎ EverList ...' / 'No listings matched ...' (smart-search)."""
    return reply.startswith("֎ EverList") or reply.startswith("No listings matched")


# ---- required command coverage (backlog B10 list) ----
REQUIRED = ["search", "list", "signup", "login-seed", "login", "email-bind", "email-code",
            "recover", "recover-confirm", "logout-all", "my-listings", "edit", "delete",
            "archive", "unarchive", "whoami", "fee", "set-payout", "verify-midnight"]
missing = [c for c in REQUIRED if c not in chatlib._HELP]
check("B10 help covers every required command", not missing, f"missing: {missing}")
check("B10 help mentions seed-shown-once for signup", "shown ONCE" in chatlib._HELP)

# ---- discovery: print the true reply of every documented command ----
PROBE = [
    ("signup", "b10-a"),
    ("login-seed", "f" * 64),
    ("login", "acct-doesnotexist"),
    ("email-bind", "b10@example.com"),
    ("email-code", "4F2A91"),
    ("recover", "nobody-b10@example.com"),
    ("recover-confirm", "x@example.com ABC123"),
    ("logout-all", ""),
    ("my-listings", ""),
    ("edit", "even-999 mgr-abc price: 5"),
    ("delete", "even-999 mgr-abc"),
    ("archive", "even-999 mgr-abc"),
    ("unarchive", "even-999 mgr-abc"),
    ("whoami", ""),
    ("set-payout", "" + "ab" * 32),
    ("verify-midnight", "3"),
    ("logout", ""),
    ("book", "even-999"),
    ("fee", ""),
    ("list", "Help Gig | meetup | 2026-10-01 | 5 | Town | 5"),
]
REPLIES = {}
for cmd, args in PROBE:
    r = chatlib.handle_text(HUB, (cmd + " " + args).strip(), "b10-help")
    REPLIES[cmd] = r
    print(f"--- {cmd}: {r[:100]!r}")

# ---- universal gate: none of the routed commands may fall through to search ----
fell = [c for c, r in REPLIES.items() if is_fallback(r)]
check("B10 no documented command falls through to search-fallback", not fell, f"fell: {fell}")

# ---- per-intent signatures (routing proof) ----
SIGS = {
    "signup": "Account created",
    "login-seed": "unknown pubkey",          # rejection, NOT a session
    "login": "❌",                            # B10 fix: rejected logins never welcome
    "whoami": "anonymously",                  # probe order: signup(auto-login) -> ... -> logout-all
                                              # REVOKED the session before whoami -> anonymous is CORRECT
    "fee": "declare their fee openly",
    "book": "Bookings need two things",
    "set-payout": "Login first",             # probe order: logout-all ran earlier -> gate is correct
    "verify-midnight": "Login first",        # same probe order: session revoked before the probe
}
for cmd, sig in SIGS.items():
    check(f"B10 '{cmd}' hits its real intent", sig.lower() in REPLIES[cmd].lower(),
          f"reply: {REPLIES[cmd][:80]!r}")

# positive whoami: fresh chat, signup auto-login -> logged-in status
r_signup = chatlib.handle_text(HUB, "signup", "b10-whoami")
assert "Account created" in r_signup
r_who = chatlib.handle_text(HUB, "whoami", "b10-whoami")
check("B10 whoami shows logged-in account after signup", "Logged in as acct-" in r_who, r_who[:80])

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== chat-help: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
