"""HTTP-level security tests for agent-hub-v2 (plan tasks I2, H1, H2, G5).
Run: python test_hub.py [port]   (starts nothing; expects hub already running on port)
"""
import json, re, sys, urllib.request

BASE = f"http://127.0.0.1:{sys.argv[1] if len(sys.argv) > 1 else 8802}"
ADMIN_KEY = "dev-admin-key-change-me"  # dev fallback key (env unset in tests)
results = []

def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("PASS" if cond else "FAIL"), name, detail)

# --- setup: bootstrap principals (I2 interim open access) ---
st, acc = req("POST", "/access", {"agent": "test-suite-agent", "acts": ["book", "list"]})
check("access bootstrap -> 201", st == 201, str(st))
BOOK_TOK = acc.get("tokens", {}).get("book", "")
LIST_TOK = acc.get("tokens", {}).get("list", "")
BOOK_HDR = {"X-Hub-Token": BOOK_TOK}

# --- setup: one booking on evt-1, one on food-1 ---
st, book = req("POST", "/book", {"listing_id": "evt-1", "attendee": "Alice-Human",
                                 "human_verified": True}, BOOK_HDR)
check("booking created", st == 201, f"status={st}")
bid, cancel_tok, secret = book["id"], book["cancel_token"], book["booking_secret"]
st, book2 = req("POST", "/book", {"listing_id": "food-1", "buyer": "Bob-Human",
                                  "quantity": 1, "human_verified": True}, BOOK_HDR)
check("booking2 created", st == 201)
bid2 = book2["id"]

# --- H2: unguessable IDs ---
check("H2 unguessable booking id", re.fullmatch(r"bk-[0-9a-f]{24}", bid) is not None, bid)

# --- I2: confirm requires owner token ---
st, r = req("POST", f"/book/{bid}/confirm", {})
check("I2 confirm without token -> 401", st == 401, str(st))
st, r = req("POST", f"/book/{bid2}/confirm", {}, {"X-Hub-Token": "forged-token"})
check("I2 confirm with forged token -> 403", st == 403)
st, r = req("POST", f"/book/{bid2}/cancel", {}, {"X-Hub-Token": cancel_tok})
check("I2 cancel-token cannot confirm (wrong action) -> 403", st == 403)

# --- I2: admin minting + confirm ---
st, r = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid2},
            {"X-Hub-Token": ADMIN_KEY})
check("I2 admin mint confirm token -> 201", st == 201, str(st))
confirm_tok = r.get("token", "")
st, r = req("POST", f"/book/{bid2}/confirm", {}, {"X-Hub-Token": confirm_tok})
check("I2 authorized confirm -> 200 RELEASED", st == 200 and r.get("escrow") == "RELEASED")
st, r = req("POST", f"/book/{bid2}/confirm", {}, {"X-Hub-Token": confirm_tok})
check("I2 confirm token replay -> 403", st == 403)

# --- I2: cancel flow with buyer token ---
st, r = req("POST", f"/book/{bid}/cancel", {}, {"X-Hub-Token": cancel_tok})
check("I2 buyer cancel -> 200 REFUNDED", st == 200 and r.get("escrow") == "REFUNDED")
st, r = req("POST", f"/book/{bid}/cancel", {}, {"X-Hub-Token": cancel_tok})
check("I2 cancel token replay -> 403", st == 403)
st, r = req("POST", f"/book/{bid}/cancel", {})
# auth-first layering: no token -> 401 BEFORE any state inspection (no state leak to anon callers);
# the escrow-409 guard is defense-in-depth, unreachable while cancel tokens are single-use
check("I2 cancel without token -> 401 auth-first", st == 401, str(st))

# --- I2: admin endpoint protection ---
st, r = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid2})
check("I2 admin without token -> 403", st == 403)
st, r = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid2},
            {"X-Hub-Token": "wrong-admin-key"})
check("I2 admin wrong key -> 403", st == 403)

# --- H1: no credentials in URLs ---
st, r = req("GET", f"/book/{bid}?secret={secret}")
check("H1 secret in query string no longer accepted -> 403", st == 403, str(st))
st, r = req("GET", f"/book/{bid}", headers={"X-Hub-Token": secret})
check("H1 private details via header secret -> 200", st == 200 and "private_details" in r)
st, r = req("GET", f"/book/{bid}")
check("H1 private details without credential -> 403", st == 403)

# --- regression: core still works ---
st, r = req("GET", "/search?q=jazz")
check("regression search works", st == 200 and r["count"] >= 1)
st, r = req("GET", "/ledger")
check("regression ledger records releases+refunds",
      any(t.get("escrow") == "RELEASED" for t in r["ledger"]) and
      any(t.get("escrow") == "REFUNDED" for t in r["ledger"]))

failed = [n for n, ok, _ in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if failed:
    print("FAILED:", failed); sys.exit(1)
print("ALL_SECURITY_CHECKS_PASSED")
