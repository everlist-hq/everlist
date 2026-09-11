"""C5: community vertical schemas — drop-in extension point, fail-closed.

  loader:        classes.json auto-loads (HUB_SCHEMAS_DIR), prefix clas-,
                 appears in /verticals + OpenAPI-adjacent surfaces
  fail-closed:   10 bad-schema classes all REJECTED at boot, hub serves
                 traffic anyway, built-ins intact, good file still loads
  builtins-win:  a file claiming name 'events' is ignored (cannot override)
  lifecycle:     classes listing create (field_types validated: skill_level
                 nonempty, duration_minutes positive_int), category vocab
                 enforced, booking attendee/quantity flow, ID prefix clas-
  chat:          'vertical: classes' rich listing works end-to-end;
                 unknown vertical mentions community verticals in the hint
Run: python test_c5_schemas.py
"""
import atexit
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "sdk"))
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


def req(method, path, body=None, headers=None, base=None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    r = urllib.request.Request((base or HUB) + path,
                               data=(json.dumps(body).encode() if body is not None else None),
                               method=method, headers=h)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


GOOD = json.dumps({"name": "classes",
                   "required": ["title", "price", "capacity", "date", "location"],
                   "optional": ["description", "category", "tags", "url", "instructor",
                                "skill_level", "duration_minutes"],
                   "categories": ["yoga", "fitness", "dance", "cooking", "art", "other"],
                   "tracks_capacity": True,
                   "field_types": {"capacity": "positive_int", "date": "nonempty",
                                   "duration_minutes": "positive_int", "skill_level": "nonempty"},
                   "booking": {"required": ["attendee"], "fields": ["attendee", "quantity"],
                                "identity": "attendee", "action": "register+pay"}})

# 10 rejection classes — every one must be refused at boot, hub unharmed
BAD = {
    "override_builtin": json.dumps({"name": "events", "required": ["title", "price"],
                                    "categories": ["x"],
                                    "booking": {"required": ["a"], "fields": ["a"],
                                                 "identity": "a", "action": "b"}}),
    "unknown_top_keys": json.dumps({"name": "badone", "required": ["title", "price"],
                                    "categories": ["x"], "hax": True,
                                    "booking": {"required": ["a"], "fields": ["a"],
                                                 "identity": "a", "action": "b"}}),
    "bad_name": json.dumps({"name": "1bad", "required": ["title", "price"], "categories": ["x"],
                            "booking": {"required": ["a"], "fields": ["a"],
                                         "identity": "a", "action": "b"}}),
    "no_price_required": json.dumps({"name": "badtwo", "required": ["title"], "categories": ["x"],
                                     "booking": {"required": ["a"], "fields": ["a"],
                                                  "identity": "a", "action": "b"}}),
    "server_owned_fields": json.dumps({"name": "badthree", "required": ["title", "price", "owner"],
                                       "categories": ["x"],
                                       "booking": {"required": ["a"], "fields": ["a"],
                                                    "identity": "a", "action": "b"}}),
    "req_opt_overlap": json.dumps({"name": "badfour", "required": ["title", "price"],
                                   "optional": ["title"], "categories": ["x"],
                                   "booking": {"required": ["a"], "fields": ["a"],
                                                "identity": "a", "action": "b"}}),
    "bad_field_type": json.dumps({"name": "badfive", "required": ["title", "price", "w"],
                                  "categories": ["x"], "field_types": {"w": "exec()"},
                                  "booking": {"required": ["a"], "fields": ["a"],
                                               "identity": "a", "action": "b"}}),
    "capacity_without_track": json.dumps({"name": "badsix", "required": ["title", "price"],
                                          "categories": ["x"], "tracks_capacity": True,
                                          "booking": {"required": ["a"], "fields": ["a"],
                                                       "identity": "a", "action": "b"}}),
    "reserved_booking_field": json.dumps({"name": "badseven", "required": ["title", "price"],
                                          "categories": ["x"],
                                          "booking": {"required": ["escrow"], "fields": ["escrow"],
                                                       "identity": "escrow", "action": "b"}}),
    "identity_not_required": json.dumps({"name": "badeight", "required": ["title", "price"],
                                         "categories": ["x"],
                                         "booking": {"required": [], "fields": ["a"],
                                                      "identity": "a", "action": "b"}}),
}


def spawn(schemas: dict):
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hub-c5-")
    sdir = os.path.join(tmp, "schemas")
    os.makedirs(sdir)
    for fname, content in schemas.items():
        with open(os.path.join(sdir, fname), "w") as fh:
            fh.write(content)
    with open(os.path.join(sdir, "not-json.txt"), "w") as fh:
        fh.write("junk")  # non-json files ignored
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "HUB_SCHEMAS_DIR": sdir, "PYTHONUNBUFFERED": "1"}
    logf = open(os.path.join(tmp, "hub.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


# ---- phase 1: ALL bad files + good file together — hub must survive and load only classes
all_files = {f"bad{i}.json": c for i, c in enumerate(BAD.values())}
all_files["classes.json"] = GOOD
HUB, _p, _log = spawn(all_files)

st, vert = req("GET", "/verticals")
vs = vert.get("verticals", {})
check("C5 hub boots with 10 bad + 1 good schema file", st == 200)
check("C5 good community schema loads", "classes" in vs, sorted(vs.keys()))
check("C5 all built-ins intact", all(v in vs for v in ("events", "food", "services")), sorted(vs.keys()))
check("C5 NO bad vertical leaked into the registry",
      not any(v.startswith("bad") for v in vs) and "events" in vs
      and vs["events"]["required"] == ["title", "date", "location", "price", "capacity"],
      str(vs.get("events", {}).get("required")))
st, health = req("GET", "/.well-known/agent-hub.json")
check("C5 hub fully serves traffic despite bad files", st == 200)

# ---- classes lifecycle
OWN = None
st, res = req("POST", "/access", {"agent": "c5-owner", "acts": ["list", "book"]})
assert st in (200, 201)
OWN = res["tokens"]["list"]
BOOK = res["tokens"]["book"]

# field_types: skill_level nonempty + duration_minutes positive_int are enforced
st, res = req("POST", "/listings", {"vertical": "classes", "title": "Morning Vinyasa",
                                    "price": 12, "capacity": 10, "date": "2026-12-01",
                                    "location": "Vienna", "category": "yoga",
                                    "instructor": "Ana", "skill_level": "beginner",
                                    "duration_minutes": 60}, headers={"X-Hub-Token": OWN})
check("C5 classes listing created (prefix clas-)", st == 201 and res.get("id", "").startswith("clas-"),
      f"{st} {str(res)[:120]}")
CLID = res.get("id", "")

st, res = req("POST", "/listings", {"vertical": "classes", "title": "Bad Level",
                                    "price": 5, "capacity": 5, "date": "2026-12-02",
                                    "location": "Vienna", "skill_level": ""},
              headers={"X-Hub-Token": OWN})
check("C5 skill_level nonempty enforced (400)", st == 400, f"{st} {res}")

st, res = req("POST", "/listings", {"vertical": "classes", "title": "Bad Duration",
                                    "price": 5, "capacity": 5, "date": "2026-12-02",
                                    "location": "Vienna", "duration_minutes": -3},
              headers={"X-Hub-Token": OWN})
check("C5 duration_minutes positive_int enforced (400)", st == 400, f"{st} {res}")

st, res = req("POST", "/listings", {"vertical": "classes", "title": "Bad Category",
                                    "price": 5, "capacity": 5, "date": "2026-12-02",
                                    "location": "Vienna", "category": "quantum"},
              headers={"X-Hub-Token": OWN})
check("C5 classes category vocabulary enforced (400)", st == 400, f"{st} {res}")

# booking flow: attendee identity + quantity, capacity tracked
st, res = req("POST", "/book", {"listing_id": CLID, "attendee": "Claire", "quantity": 2,
                                "human_verified": True}, headers={"X-Hub-Token": BOOK})
check("C5 classes booking works (HELD, capacity tracked)",
      st == 201 and res.get("escrow") == "HELD", f"{st} {str(res)[:120]}")

st, lst = req("GET", f"/listings/{CLID}")
check("C5 registered=2 after booking (tracks_capacity)",
      st == 200 and lst.get("registered") == 2, f"{lst.get('registered')}")

st, res = req("POST", "/book", {"listing_id": CLID, "attendee": "Zed",
                                "quantity": "x", "human_verified": True},
              headers={"X-Hub-Token": BOOK})
check("C5 booking quantity strict-int still applies to community verticals",
      st == 400, f"{st} {res}")

# ---- phase 2: chat path (fresh hub, clean state)
_p.terminate()
HUB2, _p2, _log2 = spawn({"classes.json": GOOD})
import chatlib  # noqa: E402

r = chatlib.handle_text(HUB2, "list\nvertical: classes\ntitle: Evening Salsa\nprice: 9\n"
                              "date: 2026-12-10\nlocation: Vienna\ncapacity: 14\n"
                              "instructor: Marco\nskill_level: all levels\n"
                              "category: dance\ndescription: fun class", "c5-chat-owner")
check("C5 chat creates a classes listing", "clas-" in r, r[:120])

r = chatlib.handle_text(HUB2, "list\nvertical: nope\ntitle: X\nprice: 1", "c5-chat-owner")
check("C5 unknown-vertical hint mentions community verticals",
      "Unknown vertical" in r and "classes" in r, r[:140])

st, res = req("GET", f"/search?q=salsa", base=HUB2)  # phase-2 hub (HUB was terminated)
hits = res.get("listings", [])
check("C5 chat-created classes listing searchable",
      st == 200 and len(hits) == 1 and hits[0]["id"].startswith("clas-"),
      f"{st} {[h.get('id') for h in hits]}")

print(f"\n=== C5 schemas: {sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} passed ===")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("C5_SCHEMAS_PASSED")
