"""H15: second vertical services - the extensibility proof (backlog v2).

Claim under test: adding a vertical is DATA, not code. One schema entry
extends listing validation, booking fields, identity privacy projection,
ID prefix, chat flows and the SDK - with zero new route code.

Proven end to end:
  hub   /verticals reflects services (identity=client, no capacity tracking)
  SDK   signup -> services listing (price 0) -> book -> escrow WAIVED
        -> owner /orders shows pseudonymous client ref -> private details
        via secret -> confirm -> RELEASED (WAIVED confirm) -> cancel REPLAY:
        second booking cancelled (WAIVED cancel fix)
  chat  signup (PoW, auto-login) -> rich list vertical:services -> book in chat
        executes for FREE listings with a vouched account (server-side proof)
  honesty  unknown vertical / missing provider prompts; PAID listing in chat
        -> guidance (payment is a real gate); booking allowlist DERIVED from
        schema (events field on a services booking -> 400)
Run: python test_h15_services.py
"""
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
sys.path.insert(0, os.path.join(HERE, "sdk"))
sys.path.insert(0, HERE)
from agenthub import AgentHub, HubError  # noqa: E402
import chatlib  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(HUB + path, data=data, method=method,
                               headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": str(e)}


PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-h15-")
HUB = f"http://127.0.0.1:{PORT}"
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1"}
logf = open(os.path.join(TMP, "hub.log"), "a")
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=logf, stderr=subprocess.STDOUT, env=env)
end = time.time() + 15
ready = False
while time.time() < end:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
            ready = True
            break
    except OSError:
        time.sleep(0.2)
assert ready, "test hub did not start"

try:
    print("== H15: vertical registry reflects services (data, not code) ==")
    st, v = req("GET", "/verticals")
    sv = v.get("verticals", {}).get("services", {})
    check("GET /verticals -> 200 and services present", st == 200 and bool(sv), str(st))
    check("services booking identity field = client", sv.get("booking", {}).get("identity") == "client")
    check("services is not capacity-tracked", not sv.get("tracks_capacity"))

    print("== H15: SDK E2E - free services booking, full lifecycle ==")
    owner = AgentHub(HUB)
    owner.signup_keypair("h15-owner")
    lst = owner.add_listing(vertical="services", title="Mobile Massage", price=0.0,
                            provider="Serenity Spa", category="wellness",
                            location="Vienna", duration_minutes=60,
                            description="Relaxing massage at your home")
    check("services listing id uses per-vertical prefix", lst.id.startswith("serv-"), lst.id)
    buyer = AgentHub(HUB)
    buyer.signup_keypair("h15-buyer")
    bk = buyer.book(lst.id, client="Anna", human_verified=True)
    check("SDK booking accepted, escrow WAIVED at price 0",
          bk.escrow == "WAIVED" and bk.amount == 0.0, f"{bk.escrow}/{bk.amount}")
    st, orders = req("GET", "/orders", headers={"X-Hub-Token": owner._token("list")})
    mine = [o for o in orders.get("orders", []) if o.get("id") == bk.id]
    check("owner /orders shows the services booking", len(mine) == 1, str(len(mine)))
    check("client identity pseudonymized in /orders (no Anna leak)",
          mine and str(mine[0].get("client", "")).startswith("anon-")
          and "Anna" not in json.dumps(orders), str(mine[0] if mine else orders))
    pd = buyer.private_details(bk.id, bk.secret)
    check("real identity retrievable ONLY via booking secret",
          pd.get("private_details", {}).get("client") == "Anna", str(pd))
    st, mt = req("POST", "/admin/tokens", {"act": "confirm", "booking_id": bk.id},
                 headers={"X-Hub-Token": "dev-admin-key-change-me"})
    st2, cf = req("POST", f"/book/{bk.id}/confirm", {},
                  headers={"X-Hub-Token": mt.get("token", "")})
    check("owner confirm on WAIVED booking -> RELEASED (H15 fix)",
          st2 == 200 and cf.get("escrow") == "RELEASED", f"{st2} {cf}")
    bk2 = buyer.book(lst.id, client="Bob", human_verified=True)
    c = buyer.cancel(bk2.id, bk2.cancel_token)
    check("cancel works on WAIVED bookings (H15 fix)", c.get("escrow") == "REFUNDED", str(c.get("escrow")))

    print("== H15: chat E2E - services listing + booking IN CHAT ==")
    reply = chatlib.handle_text(HUB, "signup", "h15-chat")
    m = re.search(r"[0-9a-f]{64}", reply)
    check("chat signup returns seed once (session active)", bool(m), reply[:120])
    aid_m = re.search(r"acct-[0-9a-f]{8}", reply)
    check("chat signup returns account id", bool(aid_m), reply[:120])
    st, vres = req("POST", "/accounts/vouch", {"account_id": aid_m.group(0)},
                   headers={"X-Admin-Key": "dev-admin-key-change-me"})
    check("operator vouch OK (pilot human proof)", st == 200 and vres.get("human_verified"), f"{st} {vres}")
    rich = """list
vertical: services
title: Home Repair Visit
provider: FixIt Co
price: 0
category: repair
location: Linz
duration_minutes: 90
description: Small repairs around the house"""
    reply = chatlib.handle_text(HUB, rich, "h15-chat")
    lid_m = re.search(r"serv-\d+", reply)
    check("chat rich listing with vertical:services accepted", bool(lid_m), reply[:160])
    lid = lid_m.group(0) if lid_m else ""
    reply = chatlib.handle_text(HUB, "book " + lid + " Anna", "h15-chat")
    check("chat books the FREE services listing in-chat",
          reply.startswith("✅ Booked!") and "WAIVED" in reply and "bk-" in reply, reply[:200])

    print("== H15: honesty guards ==")
    reply = chatlib.handle_text(HUB, "list\nvertical: cruises\ntitle: X", "h15-chat")
    check("unknown vertical -> helpful rejection", "Unknown vertical" in reply, reply[:120])
    reply = chatlib.handle_text(HUB, "list\nvertical: services\ntitle: X", "h15-chat")
    check("services without provider -> guided example", "provider" in reply, reply[:120])
    lst_paid = owner.add_listing(vertical="services", title="Deep Clean", price=25.0,
                                 provider="FixIt Co", category="cleaning")
    reply = chatlib.handle_text(HUB, "book " + lst_paid.id + " Anna", "h15-chat")
    check("PAID listing in chat -> honest guidance (payment real gate)",
          "PAID listing" in reply and "x402" in reply, reply[:140])
    try:
        buyer.book(lst_paid.id, client="Anna", attendee="Anna", human_verified=True)
        check("events-only field rejected on services booking", False, "booking accepted")
    except HubError as ex:
        check("booking allowlist DERIVED from schema (attendee -> 400)",
              ex.status == 400 and "unknown fields" in str(getattr(ex, "body", {}).get("error", "")),
              f"{getattr(ex, 'status', '?')} {getattr(ex, 'body', {})}")
    st, dup = req("GET", "/verticals")
    _vs = dup.get("verticals", {})
    # C5: community verticals are ADDITIVE by design (classes ships by default
    # now) - the contract is: built-ins all present with identical content,
    # nothing removed, extras allowed.
    check("built-in schemas unchanged (additive-only contract, C5)",
          all(v in _vs for v in ("events", "food", "services"))
          and _vs.get("events", {}).get("required") == ["title", "date", "location", "price", "capacity"],
          str(list(_vs.keys())))

finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print()
print(f"=== h15-services: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H15_SERVICES_ALL_PASSED")
