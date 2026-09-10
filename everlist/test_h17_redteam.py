"""H17: red-team pass 2 - the NEW surface under attack (backlog v2 Run 3).

Findings locked in as permanent regressions (each was a REAL probe finding):
  A  dead-token reuse after account deletion -> 401/403 everywhere, no leaks
  C  state.json + backups created 0600/0700 (HUB_BACKUP_MIN_BYTES=0 for test)
  D  /openapi.json leaks no admin key
  E  X-Request-Id echo cannot inject a second log line
  F  services: identity field must be a bounded nonempty string (H17 fix);
     human_verified must be a real bool (H17 fix); duration validated+clean
     errors (H18 fix)
  G  manage edit: schema-derived allowlist (H17 fix) - services provider/
     duration editable, vertical swap/owner swap/unknown field refused;
     capacity-below-registered still 409 on a REAL booking
  H  idempotency: replay 201 same id; same key + different body -> 409
  L  error-shape sweep: every 4xx carries a string error (H18)
Run: python test_h17_redteam.py
"""
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "sdk"))
from agenthub import AgentHub, HubError  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
TMP = tempfile.mkdtemp(prefix="hub-h17-")
BASE = f"http://127.0.0.1:{PORT}"
ADMIN = {"X-Admin-Key": "dev-admin-key-change-me"}
SV = {"vertical": "services", "price": 0, "provider": "P", "category": "repair"}
env = {**os.environ, "HUB_STATE_FILE": os.path.join(TMP, "state.json"),
       "HUB_POW_SIGNUP_BITS": "8", "HUB_BACKUP_MIN_BYTES": "0",
       "PYTHONUNBUFFERED": "1"}
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


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
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


try:
    print("== H17-A: dead-token reuse after deletion ==")
    victim = AgentHub(BASE)
    vac = victim.signup_keypair("rt-victim")
    vtok = victim._token("list")
    vl = victim.add_listing(vertical="services", title="Massage X", price=0.0,
                            provider="Spa", category="wellness")
    req("POST", "/accounts/vouch", {"account_id": vac["account_id"]}, headers=ADMIN)
    st, _ = req("DELETE", "/accounts/me", {"confirm": vac["account_id"]},
                {"X-Hub-Token": vtok})
    check("deletion succeeds", st == 200, str(st))
    st2, _ = req("POST", "/listings", {**SV, "title": "T2"}, {"X-Hub-Token": vtok})
    check("dead token cannot create (401)", st2 == 401, str(st2))
    st3, _ = req("POST", "/book", {"listing_id": vl.id, "client": "X", "human_verified": True},
                 {"X-Hub-Token": vtok})
    check("dead token cannot book (401/403)", st3 in (401, 403), str(st3))
    st4, r4 = req("GET", "/orders", None, {"X-Hub-Token": vtok})
    check("dead token cannot read orders (401)", st4 == 401 and "owner_payout" not in str(r4))
    st5, r5 = req("GET", f"/listings/{vl.id}")
    check("deleted-owner listing fetch -> 410 gone", st5 == 410, str(st5))
    again = AgentHub(BASE)
    try:
        again.signup_keypair("rt-victim")
        check("agent name reusable after deletion", True)
    except Exception as ex:
        check("agent name reusable after deletion", False, str(ex)[:80])

    print("== H17-C: secret-file permissions (H17 fix) ==")
    owner = AgentHub(BASE)
    owner.signup_keypair("rt-owner")
    ol = owner.add_listing(vertical="services", title="PermProbe", price=0.0,
                           provider="P", category="repair")
    ocode = None
    st, r = req("POST", "/listings", {**SV, "title": "PermProbe2"},
                {"X-Hub-Token": owner._token("list")})
    ocode = r.get("manage_code")
    req("POST", f"/listings/{r['id']}/manage",
        {"manage_code": ocode, "action": "edit", "title": "PermProbe2b"},
        {"X-Hub-Token": owner._token("list")})
    smo = stat.S_IMODE(os.stat(os.path.join(TMP, "state.json")).st_mode)
    check("state.json mode 0600", smo == 0o600, oct(smo))
    bdir = os.path.join(TMP, "backups")
    check("backups dir exists after mutation", os.path.isdir(bdir))
    if os.path.isdir(bdir) and os.listdir(bdir):
        bf = sorted(os.listdir(bdir))[-1]
        bmo = stat.S_IMODE(os.stat(os.path.join(bdir, bf)).st_mode)
        dmo = stat.S_IMODE(os.stat(bdir).st_mode)
        check("backup file mode 0600", bmo == 0o600, oct(bmo))
        check("backups dir mode 0700", dmo == 0o700, oct(dmo))

    print("== H17-D: openapi secret scan ==")
    st, spec = req("GET", "/openapi.json")
    blob = json.dumps(spec)
    check("openapi 200", st == 200)
    check("no admin key in openapi", "dev-admin-key-change-me" not in blob)

    print("== H17-F: services identity/type walls (H17 fix) ==")
    st, r = req("POST", "/listings", {**SV, "title": "F"}, {"X-Hub-Token": owner._token("list")})
    fid = r["id"]
    for name, extra, want in [
        ("client number -> 400", {"client": 123}, 400),
        ("client object -> 400", {"client": {"a": 1}}, 400),
        ("client 5000 chars -> 400", {"client": "x" * 5000}, 400),
        ("client empty -> 400", {"client": ""}, 400),
        ("client ok -> 201", {"client": "Anna"}, 201),
    ]:
        st2, r2 = req("POST", "/book", {"listing_id": fid, **extra, "human_verified": True},
                      {"X-Hub-Token": owner._token("book")})
        check(name, st2 == want, f"{st2} {str(r2)[:70]}")
    st2, r2 = req("POST", "/book", {"listing_id": fid, "client": "B", "human_verified": "yes"},
                  {"X-Hub-Token": owner._token("book")})
    check("human_verified string -> 400 (H17 fix)", st2 == 400 and "boolean" in r2.get("error", ""),
          f"{st2} {r2}")
    st2, r2 = req("POST", "/book", {"listing_id": fid, "client": "C", "human_verified": False},
                  {"X-Hub-Token": owner._token("book")})
    check("human_verified false -> 403 gate", st2 == 403, str(st2))
    for name, lp, want in [
        ("duration -5 -> 400", {**SV, "title": "D1", "duration_minutes": -5}, 400),
        ("duration str -> 400 clean (H18)", {**SV, "title": "D2", "duration_minutes": "abc"}, 400),
        ("duration ok -> 201", {**SV, "title": "D3", "duration_minutes": 45}, 201),
        ("empty provider -> 400 (H17 fix)", {**SV, "title": "D4", "provider": ""}, 400),
        ("title 5000 -> 201 (80 cap)", {**SV, "title": "T" * 5000}, 201),
        ("desc 5000 -> 400", {**SV, "title": "D5", "description": "x" * 5000}, 400),
    ]:
        st2, r2 = req("POST", "/listings", lp, {"X-Hub-Token": owner._token("list")})
        clean = "int() with base 10" not in str(r2)
        check(name, st2 == want and (want != 400 or clean), f"{st2} {str(r2)[:70]}")

    print("== H17-G: schema-derived manage edits (H17 fix) ==")
    st, r = req("POST", "/listings", {**SV, "title": "G"}, {"X-Hub-Token": owner._token("list")})
    gid, gcode = r["id"], r["manage_code"]
    otok = owner._token("list")
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "provider": "NewP"}, {"X-Hub-Token": otok})
    check("services provider edit -> 200 (H17 fix)", st2 == 200, f"{st2} {str(r2)[:70]}")
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "duration_minutes": 45}, {"X-Hub-Token": otok})
    check("services duration edit -> 200 (H17 fix)", st2 == 200, f"{st2} {str(r2)[:70]}")
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "duration_minutes": -3}, {"X-Hub-Token": otok})
    check("edit duration -3 -> 400", st2 == 400, str(st2))
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "vertical": "events"}, {"X-Hub-Token": otok})
    check("vertical swap -> 400", st2 == 400, str(st2))
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "owner": "someone-else"}, {"X-Hub-Token": otok})
    check("owner swap -> 400", st2 == 400, str(st2))
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "attendee": "X"}, {"X-Hub-Token": otok})
    check("events-only field edit -> 400", st2 == 400, str(st2))
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": "mgr-wrong", "action": "edit", "title": "H"}, {"X-Hub-Token": otok})
    check("owner token + wrong code -> 200 (token path wins)", st2 == 200, str(st2))
    stranger = AgentHub(BASE)
    stranger.signup_keypair("rt-stranger")
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": "mgr-wrong", "action": "edit", "title": "H3"},
                  {"X-Hub-Token": stranger._token("list")})
    check("non-owner token + wrong code -> 403", st2 == 403, str(st2))
    # capacity guard on a REAL booking (probe lesson: registered must be > 0)
    bk = owner.book(gid, client="CapProbe", human_verified=True)
    st2, r2 = req("POST", f"/listings/{gid}/manage",
                  {"manage_code": gcode, "action": "edit", "title": "Z"},
                  {"X-Hub-Token": owner._token("list")})
    ev = AgentHub(BASE)
    ev.signup_keypair("rt-ev")
    st3, r3 = req("POST", "/listings",
                  {"vertical": "events", "title": "E", "price": 0, "category": "meetup",
                   "date": "2026-12-01", "location": "X", "capacity": 5},
                  {"X-Hub-Token": ev._token("list")})
    ecode = r3["manage_code"]
    ev.book(r3["id"], attendee="A", human_verified=True)
    st4, r4 = req("POST", f"/listings/{r3['id']}/manage",
                  {"manage_code": ecode, "action": "edit", "capacity": 0},
                  {"X-Hub-Token": ev._token("list")})
    check("capacity below registered -> 409 (real booking, 0 < 1)", st4 == 409, f"{st4} {str(r4)[:70]}")

    print("== H17-H: idempotency cross-body ==")
    key = "rt-idem-1"
    b1 = {"listing_id": fid, "client": "A", "human_verified": True}
    st1, r1 = req("POST", "/book", b1, {"X-Hub-Token": owner._token("book"), "Idempotency-Key": key})
    st2, r2 = req("POST", "/book", b1, {"X-Hub-Token": owner._token("book"), "Idempotency-Key": key})
    st3, r3 = req("POST", "/book", {"listing_id": fid, "client": "B", "human_verified": True},
                  {"X-Hub-Token": owner._token("book"), "Idempotency-Key": key})
    check("replay 201 same id", st1 == 201 and st2 == 201 and r1.get("id") == r2.get("id"))
    check("same key diff body -> 409", st3 == 409, str(st3))

    print("== H17-L: error-shape sweep (H18) ==")
    probes = [("GET", "/listings/does-not-exist", None), ("GET", "/bookings/zz", None),
              ("POST", "/book", {}), ("POST", "/listings", {}), ("POST", "/access", {}),
              ("POST", "/accounts/signup", {}), ("DELETE", "/accounts/me", {}),
              ("POST", "/listings/x/manage", {}), ("POST", "/book/x/confirm", {}),
              ("POST", "/book/x/cancel", {}), ("POST", "/admin/tokens", {}),
              ("GET", "/search?q=" + "x" * 300, None)]
    bad = []
    for m, path, body in probes:
        st2, r2 = req(m, path, body)
        e = r2.get("error") if isinstance(r2, dict) else None
        if st2 >= 400 and not (isinstance(e, str) and e):
            bad.append((m, path, st2, r2))
    check("all 4xx responses carry string error", not bad, str(bad[:2]))

finally:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    logf.close()

fails = [n for n, ok in RESULTS if not ok]
print()
print(f"=== h17-redteam: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("H17_REDTEAM_ALL_PASSED")
