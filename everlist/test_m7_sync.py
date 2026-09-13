"""M7: mirror sync - POST /admin/sync-escrow (+ conformance checker C7).
Done-when: chain RELEASED + hub HELD -> sync fixes; chain HELD + hub RELEASED
-> flagged, refused. Plus: M16 carve-out (chain REFUNDED + hub RELEASED ->
forward-mirrored: on-chain RELEASED->REFUNDED is reachable ONLY via
mutualRefund with both commitments proven), WAIVED untouched, ledger
entries carry no amount (no volume double-count), honest indexer errors,
admin gating (403), unknown booking ids, and C7 verdict via the real checker.

Chain side = fixture indexer server (M6 pattern) serving the ESCROW_STATE_CODEC
state per (contract, escrow_id); stages flip to simulate transitions exactly
as a real chain would grow history.
Run: python test_m7_sync.py
"""
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "..", "registry"))
from midnight_indexer import EscrowIndexerClient  # noqa: E402
from check_hub import check_hub  # noqa: E402

RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix="hub-m7-")
PORT = free_port(); BASE = "http://127.0.0.1:%d" % PORT; PYEXE = sys.executable
ADMIN = "dev-admin-key-change-me"
CONTRACT = "midnight1qm7fixturecontract"

def req(method, path, body=None, headers=None):
    r = urllib.request.Request(BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception as e: return -1, {"error": str(e)}

def solve_pow(kind):
    _, ch = req("GET", "/auth/challenge?kind=" + kind)
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]: return {"challenge": ch["challenge"], "nonce": n}
        n += 1

# --- fixture chain: per-escrow stage map, flips simulate transitions ---
CHAIN = {1: "held", 2: "held", 3: "held", 4: "held"}  # escrow_id -> stage
STAGE_STATE = {"held": 1, "released": 2, "refunded": 3}

def state_hex(ids):
    esc = {str(i): {"state": STAGE_STATE[CHAIN[i]], "amount": "250",
                    "booking_ref": ("bk-m7-%d" % i).encode().hex().ljust(64, "0"),
                    "deadline": "99999999999"} for i in ids}
    return json.dumps({"escrows": esc}).encode("utf-8").hex()

class ChainHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n).decode())
        except ValueError:
            body = {}
        addr = (body.get("variables") or {}).get("addr", "")
        data = {"data": {"contractActions": {"edges": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if addr == CONTRACT:
            node = {"__typename": "ContractCall", "entryPoint": "createEscrow",
                    "state": state_hex(sorted(CHAIN)), "transaction": {"id": "aa" * 32}}
            data["data"]["contractActions"]["edges"] = [{"node": node}]
        out = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *a):
        pass

CPORT = free_port()
cserver = ThreadingHTTPServer(("127.0.0.1", CPORT), ChainHandler)
threading.Thread(target=cserver.serve_forever, daemon=True).start()
INDEXER = "http://127.0.0.1:%d" % CPORT

env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "HUB_RATE_BOOKS_PER_MIN": "1000",
       "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([PYEXE, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15; ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5): ready = True; break
    except OSError: time.sleep(0.2)
assert ready, "test hub did not start"

try:
    print("== M7 setup: owner, listings, bookings with escrow_ref ==")
    c, a = req("POST", "/accounts/signup", {"agent": "m7-owner", "pow": solve_pow("signup")})
    assert c == 201, a
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "m7-owner"})
    assert c == 200, l
    OTOK = l["tokens"]["list"]
    c, r = req("POST", "/listings", {"vertical": "events", "title": "M7 Paid",
               "date": "2026-10-10", "location": "X", "price": 5, "capacity": 10},
               {"X-Hub-Token": OTOK})
    assert c == 201, r
    PAID = r["id"]
    c, r = req("POST", "/listings", {"vertical": "events", "title": "M7 Free",
               "date": "2026-10-11", "location": "X", "price": 0, "capacity": 10},
               {"X-Hub-Token": OTOK})
    assert c == 201, r
    FREE = r["id"]
    BUYER = "agent1qm7buyer"
    c, bt = req("POST", "/access", {"agent": BUYER, "acts": ["book"]})
    assert c == 201, bt
    B = {"X-Hub-Token": bt["tokens"]["book"]}
    bids = {}
    for eid in (1, 2, 3, 4):
        c, b = req("POST", "/book", {"listing_id": PAID, "attendee": "M7 %d" % eid,
                   "human_verified": True,
                   "escrow_ref": {"contract": CONTRACT, "escrow_id": eid, "tx": "aa" * 32}}, B)
        assert c == 201, b
        bids[eid] = b["id"]
    c, b = req("POST", "/book", {"listing_id": FREE, "attendee": "M7 free",
               "human_verified": True}, B)
    assert c == 201, b
    WAIVED_BID = b["id"]
    check("free booking is WAIVED", b["escrow"] == "WAIVED", str(b.get("escrow")))

    def confirm(bid):
        c, t = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid},
                   {"X-Hub-Token": ADMIN})
        assert c == 201, t
        return req("POST", "/book/%s/confirm" % bid, {}, {"X-Hub-Token": t["token"]})

    def sync(booking_ids=None, admin=ADMIN, indexer=INDEXER):
        h = {"X-Admin-Key": admin} if admin else {}
        body = {"indexer_url": indexer}
        if booking_ids is not None:
            body["booking_ids"] = booking_ids
        return req("POST", "/admin/sync-escrow", body, h)

    def hub_state(bid):
        _, bl = req("GET", "/bookings", headers=B)
        return next(x["escrow"] for x in bl["bookings"] if x["id"] == bid)

    print("== M7: gating + validation ==")
    c, e = sync(admin="")
    check("no admin key -> 403", c == 403, "%s %s" % (c, e))
    c, e = sync(admin="wrong-key")
    check("wrong admin key -> 403", c == 403, str(c))
    c, e = req("POST", "/admin/sync-escrow", {}, {"X-Admin-Key": ADMIN})
    check("missing indexer_url -> 400", c == 400, "%s %s" % (c, e))

    print("== M7: all in-sync (chain HELD == hub HELD) ==")
    _, led0 = req("GET", "/ledger")
    vol0 = led0["totals"]["total_volume"]
    c, s = sync()
    check("sync 200", c == 200, str(c))
    acts = {r["booking_id"]: r["action"] for r in s["results"]}
    check("all four escrow bookings in-sync",
          all(acts.get(bids[i]) == "in-sync" for i in (1, 2, 3, 4)), str(acts))
    check("WAIVED booking not a sync target", WAIVED_BID not in acts, str(sorted(acts)))

    print("== M7: confirm B and D on the hub (hub now RELEASED, chain not) ==")
    c, r = confirm(bids[2]); assert c == 200, r
    c, r = confirm(bids[4]); assert c == 200, r
    check("hub B RELEASED", hub_state(bids[2]) == "RELEASED")

    print("== M7: divergence matrix ==")
    # booking 2: chain HELD, hub RELEASED -> downward, REFUSED
    # booking 4: chain flipped to REFUNDED, hub RELEASED -> M16 carve-out:
    # legal forward transition (only mutualRefund can do this on-chain)
    CHAIN[4] = "refunded"
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("chain HELD + hub RELEASED -> refused (C7)",
          res[bids[2]]["action"] == "refused" and "downward" in res[bids[2]]["reason"],
          str(res[bids[2]]))
    check("chain REFUNDED + hub RELEASED -> updated (M16 mutualRefund carve-out)",
          res[bids[4]]["action"] == "updated", str(res[bids[4]]))
    check("refusal left hub state untouched", hub_state(bids[2]) == "RELEASED")

    print("== M7: forward sync - chain wins ==")
    CHAIN[1] = "released"; CHAIN[3] = "released"
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("chain RELEASED + hub HELD -> updated",
          res[bids[1]]["action"] == "updated" and res[bids[3]]["action"] == "updated",
          str(res[bids[1]]))
    check("hub now RELEASED (booking 1)", hub_state(bids[1]) == "RELEASED")
    check("hub now RELEASED (booking 3)", hub_state(bids[3]) == "RELEASED")
    check("updated carries chain_tx", len(res[bids[1]].get("chain_tx", "")) == 64)
    _, led1 = req("GET", "/ledger")
    sync_entries = [t for t in led1["ledger"] if t.get("kind") == "escrow_sync"]
    check("ledger has 3 escrow_sync entries (2 forward + 1 M16 carve-out)",
          len(sync_entries) == 3, str(len(sync_entries)))
    check("sync entries carry from/to/chain_tx",
          all(t.get("chain_tx") for t in sync_entries)
          and sum(1 for t in sync_entries
                  if t.get("from") == "HELD" and t.get("to") == "RELEASED") == 2
          and any(t.get("from") == "RELEASED" and t.get("to") == "REFUNDED"
                  for t in sync_entries), str(sync_entries))
    check("sync entries have NO amount key", all("amount" not in t for t in sync_entries))
    check("volume unchanged by sync (no double-count)",
          led1["totals"]["total_volume"] == vol0,
          "%s vs %s" % (led1["totals"]["total_volume"], vol0))

    print("== M7: honest errors + unknown ids ==")
    c, s = sync(booking_ids=["bk-does-not-exist"])
    check("unknown booking id -> not-found",
          s["results"] and s["results"][0]["action"] == "not-found", str(s["results"]))
    c, s = sync(indexer="http://127.0.0.1:1")
    check("indexer down -> per-booking error, hub untouched",
          all(r["action"] == "error" for r in s["results"]) and hub_state(bids[1]) == "RELEASED",
          str(s["results"][:1]))

    print("== M7: conformance checker C7 (real checker, real hub) ==")
    verdict, findings = check_hub(BASE)
    c7 = [f for f in findings if f.startswith("C7")]
    check("C7 findings present", any(f.startswith("C7:") for f in c7), str(c7))
    check("C7 mirror declared OK", any("mirror declared" in f for f in c7), str(c7))
    check("C7 gating verified", any("gated (403" in f for f in c7), str(c7))
    check("verdict CONFORMANT", verdict == "CONFORMANT", verdict + " | " +
          str([f for f in findings if "FAIL" in f]))

finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()
    cserver.shutdown()

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== m7-sync: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails); sys.exit(1)
print("M7_SYNC_ALL_PASSED")
