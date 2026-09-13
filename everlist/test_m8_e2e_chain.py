"""M8: E2E chain booking - the FULL loop, hub + (simulated) chain.

Chain side = the REAL offline contract simulator (midnight-escrow/contract/
m8-scenario.mjs): funded createEscrow x2 -> releaseEscrow -> refundEscrow.
If node + contract deps are available the timeline is REGENERATED live;
otherwise the committed recorded fixture is used (skip-cleanly for CI).

Loop (done-when): book in hub -> escrow exists in simulated chain -> sync ->
confirm -> sync -> RELEASED both sides; cancel path -> REFUNDED both sides.
Includes the mid-flight C7 moment (hub ahead of chain -> sync refuses) and
ledger-volume integrity.
Run: python test_m8_e2e_chain.py
"""
import hashlib
import json
import os
import shutil
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
RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

TMP = tempfile.mkdtemp(prefix="hub-m8-")
PORT = free_port(); BASE = "http://127.0.0.1:%d" % PORT; PYEXE = sys.executable
ADMIN = "dev-admin-key-change-me"

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

# --- chain timeline: LIVE from the real simulator, else committed fixture ---
FIXTURE_PATH = os.path.join(HERE, "fixtures", "chain-timeline.json")
LIVE = False
timeline = None  # filled after bookings (refs = booking ids)


def make_chain_server(contract, actions, stage):
    class ChainHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(n).decode())
            except ValueError:
                body = {}
            addr = (body.get("variables") or {}).get("addr", "")
            edges = []
            if addr == contract:
                for act in actions[:stage[0] + 1]:
                    edges.append({"node": {"__typename": "ContractCall",
                                           "entryPoint": act["entry_point"],
                                           "state": act["state_hex"],
                                           "transaction": {"id": act["tx"]}}})
            data = {"data": {"contractActions": {"edges": edges,
                    "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
            out = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    port = free_port()
    srv = ThreadingHTTPServer(("127.0.0.1", port), ChainHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % port


try:
    print("== M8 setup: owner, listing, two paid bookings ==")
    c, a = req("POST", "/accounts/signup", {"agent": "m8-owner", "pow": solve_pow("signup")})
    assert c == 201, a
    c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "m8-owner"})
    assert c == 200, l
    OTOK = l["tokens"]["list"]
    c, r = req("POST", "/listings", {"vertical": "events", "title": "M8 Gig",
               "date": "2026-10-10", "location": "X", "price": 5, "capacity": 10},
               {"X-Hub-Token": OTOK})
    assert c == 201, r
    LID = r["id"]
    BUYER = "agent1qm8buyer"
    c, bt = req("POST", "/access", {"agent": BUYER, "acts": ["book"]})
    assert c == 201, bt
    B = {"X-Hub-Token": bt["tokens"]["book"]}
    c, ba = req("POST", "/book", {"listing_id": LID, "attendee": "M8 A",
               "human_verified": True}, B)
    assert c == 201, ba
    BID_A, CANCEL_A = ba["id"], ba["cancel_token"]
    c, bb = req("POST", "/book", {"listing_id": LID, "attendee": "M8 B",
               "human_verified": True}, B)
    assert c == 201, bb
    BID_B, CANCEL_B = bb["id"], bb["cancel_token"]

    # chain attempt NOW: the real simulator gets the booking ids as refs
    node = shutil.which("node")
    if node:
        try:
            out = tempfile.mktemp(suffix=".json")
            subprocess.run([node, "m8-scenario.mjs", out, BID_A, BID_B],
                           cwd=os.path.join(HERE, "..", "midnight-escrow", "contract"),
                           timeout=120, check=True, capture_output=True)
            timeline = json.load(open(out))
            timeline["generator"] = "LIVE regeneration (real offline funded sim)"
            LIVE = True
        except Exception as ex:
            print("   [m8] live sim unavailable: %s" % str(ex)[:80])
    if timeline is None:
        timeline = json.load(open(FIXTURE_PATH))
        timeline.setdefault("generator", "recorded fixture (offline sim dump)")
    print("   chain timeline: %s (%d actions)" % (timeline["generator"], len(timeline["actions"])))

    CONTRACT = timeline["contract"]
    ACTIONS = timeline["actions"]
    STAGE = [0]
    csrv, INDEXER = make_chain_server(CONTRACT, ACTIONS, STAGE)

    def confirm(bid):
        c, t = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bid},
                   {"X-Hub-Token": ADMIN})
        assert c == 201, t
        return req("POST", "/book/%s/confirm" % bid, {}, {"X-Hub-Token": t["token"]})

    def hub_state(bid):
        _, bl = req("GET", "/bookings", headers=B)
        return next(x["escrow"] for x in bl["bookings"] if x["id"] == bid)

    def sync():
        return req("POST", "/admin/sync-escrow", {"indexer_url": INDEXER},
                   {"X-Admin-Key": ADMIN})

    # link bookings -> chain escrows via the M5 escrow_ref (through manage-free
    # path: re-book would break ids, so refs are injected at book time here by
    # booking WITH refs from the start is impossible pre-timeline; instead the
    # test re-books two fresh bookings WITH refs once the timeline exists)
    print("== M8: bookings walk the chain timeline (escrow_ref linked) ==")
    c, ba2 = req("POST", "/book", {"listing_id": LID, "attendee": "M8 A2",
                "human_verified": True,
                "escrow_ref": {"contract": CONTRACT, "escrow_id": 1, "tx": ACTIONS[0]["tx"]}}, B)
    assert c == 201, ba2
    RA = ba2["id"]
    c, bb2 = req("POST", "/book", {"listing_id": LID, "attendee": "M8 B2",
                "human_verified": True,
                "escrow_ref": {"contract": CONTRACT, "escrow_id": 2, "tx": ACTIONS[1]["tx"]}}, B)
    assert c == 201, bb2
    RB = bb2["id"]
    # stage 1 = both creates on chain (ACTIONS[1]); hub HELD, chain HELD
    STAGE[0] = 1
    c, s = sync()
    assert c == 200, s
    res = {r["booking_id"]: r["action"] for r in s["results"]}
    check("stage create: both in-sync (chain HELD == hub HELD)",
          res.get(RA) == "in-sync" and res.get(RB) == "in-sync", str(res))

    # hub confirms BOTH bookings ahead of the chain -> hub RELEASED, chain HELD
    c, r = confirm(RA); assert c == 200, r
    c, r = confirm(RB); assert c == 200, r
    check("hub ahead: RA RELEASED", hub_state(RA) == "RELEASED")
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("C7 mid-flight: hub ahead -> REFUSED (never downward)",
          res[RA]["action"] == "refused" and res[RB]["action"] == "refused", str(res[RA]))
    check("refusal kept hub state", hub_state(RA) == "RELEASED" and hub_state(RB) == "RELEASED")

    # chain release of escrow 1 (ACTIONS[2]) -> forward sync fixes RA
    STAGE[0] = 2
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("chain RELEASED + hub RELEASED (RA): in-sync after chain caught up",
          res[RA]["action"] == "in-sync", str(res[RA]))
    check("RB still refused (chain HELD, hub RELEASED)", res[RB]["action"] == "refused")

    # chain refund of escrow 2 (ACTIONS[3]) -> RB forward-updated to REFUNDED
    STAGE[0] = 3
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("chain REFUNDED + hub RELEASED (RB) -> updated (M16 carve-out: on-chain mutualRefund is the only path)",
          res[RB]["action"] == "updated", str(res[RB]))

    # refund leg: booking C gets its OWN chain escrow 3 (the S1 duplicate-ref
    # wall rightly forbids two hub bookings on one chain escrow). The scenario
    # now runs escrow 3 create->refund as actions 5-6 (indices 4-5); advance
    # the chain stage to 5 BEFORE C's sync so escrow 3 exists on chain and is
    # REFUNDED while C's hub state is still HELD -> forward update.
    STAGE[0] = 5
    c, bc = req("POST", "/book", {"listing_id": LID, "attendee": "M8 C",
               "human_verified": True,
               "escrow_ref": {"contract": CONTRACT, "escrow_id": 3, "tx": ACTIONS[4]["tx"]}}, B)
    check("booking C created with escrow_ref 3", c == 201, str(c))
    _, led0 = req("GET", "/ledger")
    vol0 = led0["totals"]["total_volume"]
    c, s = sync()
    res = {r["booking_id"]: r for r in s["results"]}
    check("chain REFUNDED + hub HELD (C) -> updated",
          res[bc["id"]]["action"] == "updated", str(res[bc["id"]]))
    check("REFUNDED both sides (C)", hub_state(bc["id"]) == "REFUNDED")
    check("RB (same escrow, now REFUNDED both sides) in-sync",
          res[RB]["action"] == "in-sync", str(res[RB]))
    c, r = req("POST", "/book/%s/cancel" % bc["id"], {},
               {"X-Hub-Token": B["X-Hub-Token"], "X-Cancel-Token": CANCEL_B})
    check("cancel with wrong token refused", c == 403, str(c))

    print("== M8: ledger integrity after all syncs ==")
    _, led1 = req("GET", "/ledger")
    sync_entries = [t for t in led1["ledger"] if t.get("kind") == "escrow_sync"]
    check("exactly two escrow_sync entries (RB mutualRefund + C's forward update)",
          len(sync_entries) == 2, str(len(sync_entries)))
    check("sync entries: from/to/chain_tx, no amount (both, in order)",
          len(sync_entries) == 2
          and all(t.get("from") and t.get("to") and t.get("chain_tx")
                  and "amount" not in t for t in sync_entries)
          and sync_entries[0].get("from") == "RELEASED"   # RB: mutualRefund carve-out sync
          and sync_entries[0].get("to") == "REFUNDED"
          and sync_entries[-1].get("from") == "HELD",     # C: forward update
          str(sync_entries))
    check("volume unchanged by syncs", led1["totals"]["total_volume"] == vol0,
          "%s vs %s" % (led1["totals"]["total_volume"], vol0))

    print("== M8: done-when summary ==")
    check("RELEASED both sides (RA)", hub_state(RA) == "RELEASED")
    # RB: hub RELEASED, chain REFUNDED -> M16 carve-out mirrors the mutual
    # refund (on-chain RELEASED->REFUNDED is reachable only via mutualRefund,
    # both commitments proven in-circuit) - chain is source of truth
    check("RB mirrored to REFUNDED via M16 carve-out", hub_state(RB) == "REFUNDED")

finally:
    proc.terminate()
    try: proc.wait(timeout=5)
    except Exception: proc.kill()
    logf.close()
    try: csrv.shutdown()
    except Exception: pass

fails = [n for n, ok in RESULTS if not ok]
print("")
print("=== m8-e2e-chain: %d/%d passed ===" % (len(RESULTS) - len(fails), len(RESULTS)))
if fails:
    print("FAILED:", fails); sys.exit(1)
print("M8_E2E_CHAIN_ALL_PASSED" + (" (LIVE SIM)" if LIVE else " (recorded fixture)"))
