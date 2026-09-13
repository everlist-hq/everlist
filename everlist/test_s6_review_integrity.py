#!/usr/bin/env python3
"""S6: review-integrity tests for agent-hub-v2 (owner-approved anti-fake-review stack).

Runs the hub with HUB_PAY_MODE=testnet (real EIP-3009 signature verification) and
proves the four S6 layers from the approved backlog item:

  L1  self-review walls: (a) listing owner cannot rate their own listing,
      (b) x402 payment from the merchant receive wallet -> rating rejected
  L2  channel split: WAIVED (free) ratings go to free-class feedback, never
      into the paid aggregate (the cheapest farm is closed)
  L3  amount weighting: paid aggregates are weighted by settled amount;
      rating_avg != plain average when amounts differ
  L4  interlock detection: repeated payer wallet on one listing -> review_flags
      + admin review queue (manual, never auto-delete)
  H9  server-only integrity fields never reach public payloads
  DX  chat one-message listing accepts receive: (instant-rail payout wallet)
  PAY invalid X-PAYMENT on an instant booking -> 402, no booking

Run with the experiment venv (needs eth-account): venv/bin/python test_s6_review_integrity.py
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import socket

from eth_account import Account

HERE = os.path.dirname(os.path.abspath(__file__))
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"  # official base-sepolia testnet USDC

_checks = []


def check(name, cond, detail=""):
    _checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


PORT = free_port()
HUB = f"http://127.0.0.1:{PORT}"
TMP = tempfile.mkdtemp(prefix="hub-s6-")
env = {**os.environ, "HUB_PAY_MODE": "testnet", "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "w")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
assert wait_ready(PORT), "hub did not start"


def req(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request(HUB + path,
                               data=(json.dumps(body).encode() if body is not None else None),
                               method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception:
        return -1, {}


def tok(agent, act):
    st, res = req("POST", "/access", {"agent": agent, "acts": [act]})
    assert st in (200, 201), f"access failed: {st} {res}"
    return res["tokens"][act]


def build_payment(to_addr, value_units, acct):
    """Real EIP-3009 TransferWithAuthorization (same shape test_x402_settle verifies)."""
    now = int(time.time())
    auth = {"from": acct.address, "to": to_addr.lower(), "value": int(value_units),
            "validAfter": now - 60, "validBefore": now + 600,
            "nonce": "0x" + uuid.uuid4().hex + uuid.uuid4().hex[:32]}
    domain = {"name": "USD Coin", "version": "2", "chainId": 84532, "verifyingContract": USDC}
    types = {"TransferWithAuthorization": [
        {"name": "from", "type": "address"}, {"name": "to", "type": "address"},
        {"name": "value", "type": "uint256"}, {"name": "validAfter", "type": "uint256"},
        {"name": "validBefore", "type": "uint256"}, {"name": "nonce", "type": "bytes32"}]}
    signed = acct.sign_typed_data(domain_data=domain, message_types=types, message_data=auth)
    payload = {"scheme": "exact", "network": "base-sepolia", "x402Version": 1,
               "payload": {"authorization": auth, "signature": signed.signature.hex()}}
    return base64.b64encode(json.dumps(payload).encode()).decode()


ADMIN = "dev-admin-key-change-me"
MERCHANT = Account.from_key(os.urandom(32))  # the merchant receive wallet
W1 = Account.from_key(os.urandom(32))        # honest buyer wallet
W2 = Account.from_key(os.urandom(32))        # second honest buyer wallet

OWN = tok("s6-owner", "list")
B1 = tok("s6-buyer-1", "book")
B2 = tok("s6-buyer-2", "book")

# --- listing A: paid, instant rail, merchant receive wallet on the listing
st, res = req("POST", "/listings", {"vertical": "events", "title": "S6 Gala",
                                    "category": "party", "date": "2026-12-05",
                                    "price": 5, "location": "Vienna", "capacity": 20,
                                    "payment_terms": {"rail": "instant"},
                                    "receive_addr": MERCHANT.address},
              headers={"X-Hub-Token": OWN})
check("S6 listing with receive_addr created", st == 201, f"{st} {res}")
A_ID = res["id"]
_, pubA = req("GET", f"/listings/{A_ID}")
check("S6 receive_addr stored lowercase", pubA.get("receive_addr") == MERCHANT.address.lower(), str(pubA.get("receive_addr")))

st, res = req("POST", "/listings", {"vertical": "events", "title": "S6 Bad",
                                    "category": "party", "date": "2026-12-05",
                                    "price": 5, "location": "Vienna", "capacity": 5,
                                    "receive_addr": "not-an-address"},
              headers={"X-Hub-Token": OWN})
check("S6 invalid receive_addr -> 400", st == 400, f"{st} {res}")

st, res = req("POST", "/listings", {"vertical": "events", "title": "S6 Seed",
                                    "category": "party", "date": "2026-12-05",
                                    "price": 5, "location": "Vienna", "capacity": 5,
                                    "rating_sum": 999, "rating_count": 999},
              headers={"X-Hub-Token": OWN})
check("S6 client-seeded rating aggregates -> 400 (reserved)", st == 400, f"{st} {res}")

# --- H9: server-only integrity fields never public
st, pub = req("GET", f"/listings/{A_ID}")
leaked = [k for k in ("rating_wsum", "rating_wtot", "review_flags", "rating_times", "manage_code_hash", "claim_code_hash") if k in pub]
check("S6 H9 server-only integrity fields absent from public listing", st == 200 and not leaked, str(leaked))

# --- invalid x402 payment on instant booking -> 402, nothing created
bad = base64.b64encode(json.dumps({"scheme": "exact"}).encode()).decode()
st, res = req("POST", "/book", {"listing_id": A_ID, "attendee": "Zed", "human_verified": True,
                                "accepted_payment_terms": pub["payment_terms"]},
              headers={"X-Hub-Token": B1, "X-PAYMENT": bad})
check("S6 invalid X-PAYMENT -> 402", st == 402, f"{st} {res}")

# --- honest buyer: real payment from W1 -> payment evidence bound to booking
pay = build_payment(MERCHANT.address.lower(), int(5 * 1_000_000), W1)
st, res = req("POST", "/book", {"listing_id": A_ID, "attendee": "Alice", "human_verified": True,
                                "accepted_payment_terms": pub["payment_terms"]},
              headers={"X-Hub-Token": B1, "X-PAYMENT": pay})
check("S6 valid x402 instant booking -> 201 DIRECT", st == 201 and res.get("escrow") == "DIRECT", f"{st} {res}")
BK1 = res["id"]
st, priv = req("GET", f"/book/{BK1}", None, {"X-Hub-Token": res["booking_secret"]})
check("S6 payment evidence bound (payer = W1)", priv.get("private_details", {}).get("payment_payer") == W1.address.lower(),
      str(priv.get("private_details", {}).get("payment_payer")))

# --- self-pay attack: booking paid FROM the merchant receive wallet
pay_self = build_payment(MERCHANT.address.lower(), int(5 * 1_000_000), MERCHANT)
st, res = req("POST", "/book", {"listing_id": A_ID, "attendee": "Mallory", "human_verified": True,
                                "accepted_payment_terms": pub["payment_terms"]},
              headers={"X-Hub-Token": B2, "X-PAYMENT": pay_self})
check("S6 self-pay booking accepted (payment is real)", st == 201, f"{st} {res}")
BK_SELF = res["id"]
st, res = req("POST", f"/book/{BK_SELF}/rate", {"rating": 5}, headers={"X-Hub-Token": B2})
check("S6 L1b self-pay rating -> 403", st == 403 and "self-review" in res.get("error", ""), f"{st} {res}")

# --- honest buyer rates: paid channel, weighted aggregate
def book_plain(lid, buyer_tok, name, qty=1, payment=None, wallet=None):
    st, p2 = req("GET", f"/listings/{lid}")
    body = {"listing_id": lid, "human_verified": True, "quantity": qty}
    body["attendee"] = name
    if p2.get("payment_terms", {}).get("rail") == "instant" and p2["payment_terms"] != {"rail": "escrow"}:
        body["accepted_payment_terms"] = p2["payment_terms"]
    hdrs = {"X-Hub-Token": buyer_tok}
    if payment:
        hdrs["X-PAYMENT"] = payment
    st, res2 = req("POST", "/book", body, headers=hdrs)
    assert st == 201, f"book failed: {st} {res2}"
    return res2["id"]

st, res = req("POST", f"/book/{BK1}/rate", {"rating": 5}, headers={"X-Hub-Token": B1})
check("S6 honest paid rating -> 200 channel=paid", st == 200 and res.get("channel") == "paid", f"{st} {res}")
check("S6 aggregate shows weighted paid channel", "paid reviews: 5.0/5 weighted (1 rating(s))" in res.get("aggregate", ""), res.get("aggregate", ""))

# --- L1a: the owner books their own listing and tries to review it
OWN_BOOK = tok("s6-owner", "book")  # same principal as the listing owner, book act
BK_OWN = book_plain(A_ID, OWN_BOOK, "Owner")
st, res = req("POST", f"/book/{BK_OWN}/rate", {"rating": 5}, headers={"X-Hub-Token": OWN_BOOK})
check("S6 L1a owner self-review -> 403", st == 403 and "self-review" in res.get("error", ""), f"{st} {res}")

# --- L2: free bookings feed the separate feedback channel
st, res = req("POST", "/listings", {"vertical": "events", "title": "S6 Free",
                                    "category": "community", "date": "2026-12-06",
                                    "price": 0, "location": "Vienna", "capacity": 10},
              headers={"X-Hub-Token": OWN})
FREE = res["id"]
FBK = book_plain(FREE, B1, "Carol")
st, res = req("POST", f"/book/{FBK}/rate", {"rating": 4}, headers={"X-Hub-Token": B1})
check("S6 free rating -> channel=free-feedback", st == 200 and res.get("channel") == "free-feedback", f"{st} {res}")
st, pub2 = req("GET", f"/listings/{FREE}")
check("S6 free channel stored separately (4/5, count 1)",
      pub2.get("free_rating_sum") == 4 and pub2.get("free_rating_count") == 1
      and pub2.get("rating_count", 0) == 0, f"{pub2.get('free_rating_sum')},{pub2.get('free_rating_count')},{pub2.get('rating_count')}")

# --- L3: weighting - same listing, different amounts
st, res = req("POST", "/listings", {"vertical": "events", "title": "S6 Weighted",
                                    "category": "party", "date": "2026-12-07",
                                    "price": 10, "location": "Vienna", "capacity": 20,
                                    "payment_terms": {"rail": "instant"},
                                    "receive_addr": MERCHANT.address},
              headers={"X-Hub-Token": OWN})
B_ID = res["id"]
WK1 = book_plain(B_ID, B1, "Heavier", qty=1,
                 payment=build_payment(MERCHANT.address.lower(), int(10 * 1_000_000), W1))
WK2 = book_plain(B_ID, B2, "Lighter", qty=2,
                 payment=build_payment(MERCHANT.address.lower(), int(20 * 1_000_000), W2))
req("POST", f"/book/{WK1}/rate", {"rating": 5}, headers={"X-Hub-Token": B1})
st, res = req("POST", f"/book/{WK2}/rate", {"rating": 1}, headers={"X-Hub-Token": B2})
st, pub3 = req("GET", f"/listings/{B_ID}")
# weighted: (5*10 + 1*20) / 30 = 2.33 ; plain average would be 3.0
check("S6 L3 rating_avg is amount-weighted (2.33, not 3.0)",
      pub3.get("rating_avg") == 2.33 and pub3.get("rating_sum") == 6 and pub3.get("rating_count") == 2,
      str({k: pub3.get(k) for k in ("rating_avg", "rating_sum", "rating_count")}))

# --- L4: repeated payer wallet -> flagged + admin review queue
WK3 = book_plain(B_ID, B1, "Repeat", qty=1,
                 payment=build_payment(MERCHANT.address.lower(), int(10 * 1_000_000), W1))
st, res = req("POST", f"/book/{WK3}/rate", {"rating": 4}, headers={"X-Hub-Token": B1})
check("S6 third paid rating accepted (detection is not deletion)", st == 200, f"{st} {res}")
st, q = req("GET", "/admin/review-queue", None, {"X-Hub-Token": ADMIN})
entry = next((e for e in q.get("flagged", []) if e["id"] == B_ID), {})
reasons = [r for f in entry.get("flags", []) for r in f.get("reasons", [])]
check("S6 L4 repeated-payer flag in admin queue", st == 200 and any("repeated payer wallet" in r for r in reasons), str(reasons))
check("S6 L4 burst heuristic present (3 quick paid ratings)", any("burst" in r for r in reasons), str(reasons))
st, q2 = req("GET", "/admin/review-queue", None, {"X-Hub-Token": "wrong-key"})
check("S6 admin queue requires admin key", st == 200 and q2 == {} or q2.get("error"), f"{st} {q2}")

# --- H9 again over the full public surface
st, res = req("GET", "/search?q=S6")
blob = json.dumps(res)
leaked = [k for k in ("rating_wsum", "rating_wtot", "review_flags", "rating_times", "payment_payer") if k in blob]
check("S6 H9 search surface leaks nothing", st == 200 and not leaked, str(leaked))

# --- chat one-message listing with receive:
import chatlib  # noqa: E402
msg = chr(10).join(["list", "vertical: services", "title: S6 Chat Spa", "provider: S6 Provider",
                   "price: 5", "category: wellness", "receive: " + W2.address])
r = chatlib.handle_text(HUB, msg, "s6-chat")
check("S6 chat listing with receive: accepted", "Listed!" in r, r[:120])
st, res = req("GET", "/search?q=S6%20Chat%20Spa")
hit = next((x for x in res.get("listings", []) if x.get("title") == "S6 Chat Spa"), {})
check("S6 chat receive_addr landed on the listing", hit.get("receive_addr") == W2.address.lower(), str(hit.get("receive_addr")))

print()
failed = [n for n, ok in _checks if not ok]
print(f"=== S6 review integrity: {len(_checks) - len(failed)}/{len(_checks)} passed ===")
if failed:
    print("FAILED:", failed)
    proc.terminate()
    sys.exit(1)
print("S6_REVIEW_INTEGRITY_PASSED")
proc.terminate()
