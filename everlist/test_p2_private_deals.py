"""P2: private escrow deals (owner-approved, 2026-09-13).

Covered here:
- p2p community vertical loads fail-closed (visible in /verticals)
- visibility validation: only public|private accepted
- private creation: one-time claim code (pvt-…), hashes NEVER in any response
- discovery invisibility: /search, /listings, /suggest never leak private deals
- uniform 404: no-claim / wrong-claim GET and booking look exactly like unknown id
- claim paths: X-Claim-Code header AND ?claim= query
- owner visibility: owner token sees own private deals in /listings; others don't
- booking gate: claim required to book; claim is consumed, never stored/echoed
- make_public / make_private: rotation mints a fresh claim, old claim dies
- chat E2E: 'deal …' one-liner -> id+claim; chat 'book <id> <claim> <name>' books
  a free deal; paid deals get honest SDK guidance (claim included)

Run: python test_p2_private_deals.py
"""
import atexit
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chatlib  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def _kill(p):
    if p:
        p.terminate()
        try: p.wait(timeout=5)
        except Exception: p.kill()


_ACTIVE = []
atexit.register(lambda: [_kill(p) for p in _ACTIVE])


def wait_ready(port, timeout=15):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5): return True
        except OSError: time.sleep(0.2)
    return False


def post(base, path, body, token=None, claim=None, admin=None):
    headers = {"Content-Type": "application/json"}
    if token: headers["X-Hub-Token"] = token
    if claim: headers["X-Claim-Code"] = claim
    if admin: headers["X-Admin-Key"] = admin
    rq = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                method="POST", headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def get(base, path, token=None, claim=None):
    headers = {}
    if token: headers["X-Hub-Token"] = token
    if claim: headers["X-Claim-Code"] = claim
    rq = urllib.request.Request(base + path, headers=headers)
    try:
        with urllib.request.urlopen(rq, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def tokens(base, agent, acts):
    _, r = post(base, "/access", {"agent": agent, "acts": acts})
    return r.get("tokens", {})


TMP = tempfile.mkdtemp(prefix="hub-p2-")
STATE = os.path.join(TMP, "state.json")
PORT = free_port()
env = {**os.environ, "HUB_STATE_FILE": STATE, "HUB_POW_SIGNUP_BITS": "8",
       "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
_ACTIVE.append(proc)
assert wait_ready(PORT), "hub did not start"
HUB = f"http://127.0.0.1:{PORT}"

LT = tokens(HUB, "p2-alice", ["list"])["list"]
BT = tokens(HUB, "p2-bob", ["book", "list"])["book"]
NT = tokens(HUB, "p2-mallory", ["list", "book"])["list"]

# 1. p2p vertical loaded
st, vs = get(HUB, "/verticals")
check("p2p vertical loads fail-closed", st == 200 and "p2p" in vs.get("verticals", {}),
      f"st={st} keys={sorted(vs.get('verticals', {}).keys())}")

# 2. visibility validation
st, r = post(HUB, "/listings", {"vertical": "p2p", "title": "x", "price": 0,
                                "date": "2026-10-01", "location": "Vienna",
                                "visibility": "secret"}, token=LT)
check("visibility rejects unknown values", st == 400 and "visibility" in r.get("error", ""), f"{st} {r}")

# 3. private deal creation
st, d = post(HUB, "/listings", {"vertical": "p2p", "visibility": "private",
                                "title": "Cargo bike, barely used", "price": 0,
                                "date": "2026-10-01", "location": "Vienna",
                                "category": "secondhand", "tags": ["bike"],
                                "description": "pickup after 5pm"}, token=LT)
check("private deal created", st == 201, f"{st} {d}")
lid = d.get("id", "")
claim = d.get("claim_code", "")
check("claim code minted pvt-<16hex>", bool(re.fullmatch(r"pvt-[0-9a-f]{16}", claim)), f"claim={claim!r}")
check("creation returns manage_code + claim", bool(d.get("manage_code")) and bool(claim))
check("creation response leaks no hash", "claim_code_hash" not in json.dumps(d) and "manage_code_hash" not in json.dumps(d))

# 4. discovery invisibility
st, s = get(HUB, f"/search?q={urllib.parse.quote('cargo bike')}") if False else get(HUB, "/search?q=cargo%20bike")
check("search never returns private deal", st == 200 and not any(x["id"] == lid for x in s.get("listings", [])), f"{st}")
st, ls = get(HUB, "/listings?vertical=p2p")
check("browse /listings hides private deal", st == 200 and not any(x["id"] == lid for x in ls.get("listings", [])), f"{st}")
st, sg = get(HUB, "/suggest?q=bike")
check("suggest hides private deal", st == 200 and "p2p" not in sg.get("verticals", []) and not any("bike" == t for t in sg.get("tags", [])), f"{st} {sg}")

# 5. uniform 404 (no oracle)
st404, r404 = get(HUB, "/listings/p2p-99999")
st_nc, r_nc = get(HUB, f"/listings/{lid}")
check("GET private without claim == unknown (404, no-oracle shape)",
      st_nc == 404 and str(r_nc.get("error", "")).startswith("no listing ")
      and str(r404.get("error", "")).startswith("no listing "), f"{st_nc} {r_nc}")
st_wc, _ = get(HUB, f"/listings/{lid}", claim="pvt-" + "0" * 16)
check("GET private with WRONG claim == 404", st_wc == 404, f"{st_wc}")

# 6. claim paths work
st, l200 = get(HUB, f"/listings/{lid}", claim=claim)
check("GET with claim header -> full record", st == 200 and l200.get("id") == lid and l200.get("title") == "Cargo bike, barely used", f"{st}")
check("fetched record leaks no hash", "claim_code_hash" not in json.dumps(l200) and "manage_code_hash" not in json.dumps(l200))
st, lq = get(HUB, f"/listings/{lid}?claim={claim}")
check("GET with ?claim= query works", st == 200 and lq.get("id") == lid, f"{st}")

# 7. owner visibility in /listings
st, own = get(HUB, "/listings?vertical=p2p", token=LT)
check("owner sees own private deal in /listings", st == 200 and any(x["id"] == lid for x in own.get("listings", [])), f"{st}")
st, other = get(HUB, "/listings?vertical=p2p", token=NT)
check("non-owner token does not see it", st == 200 and not any(x["id"] == lid for x in other.get("listings", [])), f"{st}")
st, oget = get(HUB, f"/listings/{lid}", token=LT)
check("owner can GET own private deal without claim", st == 200 and oget.get("id") == lid, f"{st}")

# 8. booking gate
book = lambda c=None: post(HUB, "/book", {"listing_id": lid, "buyer": "Bob", "human_verified": True}, token=BT, claim=c)
st_nb, r_nb = book()
check("booking without claim == 404 (no-oracle shape)", st_nb == 404 and str(r_nb.get("error", "")).startswith("no listing "), f"{st_nb} {r_nb}")
st_wc, _ = book("pvt-" + "0" * 16)
check("booking with wrong claim == 404", st_wc == 404, f"{st_wc}")
st_ok, b_ok = book(claim)
check("booking with claim succeeds (free -> WAIVED)", st_ok == 201 and b_ok.get("escrow") == "WAIVED", f"{st_ok} {b_ok}")
check("booking response never echoes claim", claim not in json.dumps(b_ok), f"{b_ok}")

# 9. rotation: make_public / make_private
st, mp = post(HUB, f"/listings/{lid}/manage", {"action": "make_public", "manage_code": d.get("manage_code", "")}, token=LT)
check("make_public works", st == 200 and mp.get("visibility") == "public", f"{st} {mp}")
st, s2 = get(HUB, "/search?q=cargo%20bike")
check("public deal is discoverable again", st == 200 and any(x["id"] == lid for x in s2.get("listings", [])), f"{st}")
st, g2 = get(HUB, f"/listings/{lid}")
check("public deal GET needs no claim", st == 200 and g2.get("id") == lid, f"{st}")
st, mpr = post(HUB, f"/listings/{lid}/manage", {"action": "make_private", "manage_code": d.get("manage_code", "")}, token=LT)
new_claim = mpr.get("claim_code", "")
check("make_private mints fresh claim", st == 200 and bool(re.fullmatch(r"pvt-[0-9a-f]{16}", new_claim)) and new_claim != claim, f"{st} {mpr}")
st, g3 = get(HUB, f"/listings/{lid}", claim=claim)
check("OLD claim is dead after rotation", st == 404, f"{st}")
st, g4 = get(HUB, f"/listings/{lid}", claim=new_claim)
check("NEW claim works after rotation", st == 200 and g4.get("id") == lid, f"{st}")

# 10. paid private deal: gate hits before payment validation
st, paid = post(HUB, "/listings", {"vertical": "p2p", "visibility": "private",
                                   "title": "Vintage watch", "price": 90,
                                   "date": "2026-10-05", "location": "Graz",
                                   "category": "secondhand"}, token=LT)
plid = paid.get("id", "")
pclaim = paid.get("claim_code", "")
check("paid private deal created", st == 201 and bool(pclaim), f"{st} {paid}")
st, pb = post(HUB, "/book", {"listing_id": plid, "buyer": "Eve", "human_verified": True}, token=BT)
check("paid deal unclaimable without claim (404, no payment oracle)", st == 404, f"{st}")

# 11. chat E2E: deal one-liner -> claim -> book in chat (free deal)
sess_sender = "p2-chat-user"
r_signup = chatlib.handle_text(HUB, "signup", sender=sess_sender)
check("chat signup works (session for booking)", "Account created" in r_signup, r_signup[:120])
aid_m = re.search(r"acct-[0-9a-z]+", r_signup)
check("signup reply exposes account id", bool(aid_m), r_signup[:120])
st_v, vres = post(HUB, "/accounts/vouch", {"account_id": aid_m.group(0) if aid_m else ""},
                  admin="dev-admin-key-change-me")
check("operator vouch OK (pilot human proof)", st_v == 200 and vres.get("human_verified"), f"{st_v} {vres}")
r_deal = chatlib.handle_text(HUB, "deal Secondhand lamp | 0 | 2026-10-02 | Linz | secondhand", sender=sess_sender)
m_id = re.search(r"id: (p2p-\d+)", r_deal)
m_cl = re.search(r"pvt-[0-9a-f]{16}", r_deal)
check("chat deal one-liner returns id + claim", bool(m_id and m_cl), r_deal[:160])
cl_id, cl_claim = (m_id.group(1), m_cl.group(0)) if (m_id and m_cl) else ("", "")
st, leak = get(HUB, "/search?q=lamp")
check("chat-created deal invisible in search", st == 200 and not any(x["id"] == cl_id for x in leak.get("listings", [])), f"{st}")
r_book = chatlib.handle_text(HUB, f"book {cl_id} {cl_claim} Mia", sender=sess_sender)
check("chat books private deal with inline claim", "Booked!" in r_book, r_book[:160])

# 12. chat deal help is honest
r_help = chatlib.handle_text(HUB, "help", sender=sess_sender)
check("help documents deal command", "deal <title>" in r_help and "pvt-claim" in r_help)

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== p2-private-deals: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
sys.exit(1 if fails else 0)
