"""C2: search upgrades — date range (from/to), price range (min/max),
location substring, multi-tag (tags=, any-of), sort (date/price/newest).
Every filter proven with a MATCH and a NO-MATCH case; chat natural-language
parse path (under/over/from/until/cheapest/soonest) covered via real chatlib;
OpenAPI /search note updated.
Run: python test_c2_search.py
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


def req(base, path):
    try:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read().decode())
        except Exception: return e.code, {}
    except Exception:
        return -1, {}


def spawn(extra_env=None):
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hub-c2-")
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1", **(extra_env or {})}
    logf = open(os.path.join(tmp, "hub.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


def seed(base):
    """Seed 4 listings, all titled with the unique word 'C2Suite' so every
    assertion is isolated from the hub's 4 built-in demo listings.
    A/B/C are events WITH dates; D is a service WITHOUT a date."""
    r = urllib.request.Request(base + "/access", method="POST",
                               data=json.dumps({"agent": "c2-seeder", "acts": ["list"]}).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        tok = json.loads(resp.read().decode())["tokens"]["list"]

    def mk(title, vert, cat, date, price, loc, tags, extra=None):
        body = {"vertical": vert, "title": title, "category": cat, "price": price,
                "location": loc, "tags": tags}
        if date:
            body["date"] = date
        if vert == "events":
            body["capacity"] = 10
        if extra:
            body.update(extra)
        rq = urllib.request.Request(base + "/listings", data=json.dumps(body).encode(),
                                    method="POST", headers={"Content-Type": "application/json",
                                                            "X-Hub-Token": tok})
        with urllib.request.urlopen(rq, timeout=10) as resp:
            res = json.loads(resp.read().decode())
        assert resp.status == 201, f"seed failed: {res}"
        return res["id"]

    ids = {
        "A": mk("C2Suite Alpha Concert", "events", "concert", "2026-10-01", 10, "Berlin", ["music", "live"]),
        "B": mk("C2Suite Beta Workshop", "events", "workshop", "2026-11-15", 5, "Munich", ["tech"]),
        "C": mk("C2Suite Gamma Meetup", "events", "meetup", "2026-12-31", 0, "Berlin", ["free", "community"]),
        "D": mk("C2Suite Delta Repair", "services", "repair", None, 20, "Berlin", ["handy"],
                {"provider": "Delta Fix"}),
    }
    return ids


HUB, _p, _log = spawn()
IDS = seed(HUB)


def ids_of(path):
    st, res = req(HUB, path)
    return st, [l["id"] for l in res.get("listings", [])], res


# ---- date range: from ----
st, got, res = ids_of("/search?from=2026-11-01&q=c2suite")
check("C2 from match (>= date, events only)", st == 200 and got == [IDS["B"], IDS["C"]],
      f"{st} {got}")
check("C2 from no-match (future beyond all)", ids_of("/search?from=2027-06-01&q=c2suite")[1] == [])
check("C2 from excludes dateless services", IDS["D"] not in got, f"got={got}")
check("C2 from echo in filters", res.get("filters", {}).get("from") == "2026-11-01")

# ---- date range: to ----
st, got, _ = ids_of("/search?to=2026-11-30&q=c2suite")
check("C2 to match (<= date, dateless excluded)", st == 200 and got == [IDS["A"], IDS["B"]], f"{st} {got}")
check("C2 to no-match (past)", ids_of("/search?to=2026-01-01&q=c2suite")[1] == [])

# ---- combined range ----
st, got, _ = ids_of("/search?from=2026-10-15&to=2026-12-01&q=c2suite")
check("C2 from+to combined", st == 200 and got == [IDS["B"]], f"{got}")

# ---- price range ----
st, got, _ = ids_of("/search?min_price=8&q=c2suite")
check("C2 min_price match", st == 200 and got == [IDS["A"], IDS["D"]], f"{got}")
check("C2 min_price no-match", ids_of("/search?min_price=100&q=c2suite")[1] == [])
st, got, _ = ids_of("/search?max_price=5&q=c2suite")
check("C2 max_price match", st == 200 and got == [IDS["B"], IDS["C"]], f"{got}")
check("C2 max_price=0 (free only)", ids_of("/search?max_price=0&q=c2suite")[1] == [IDS["C"]])
st, got, _ = ids_of("/search?min_price=4&max_price=12&q=c2suite")
check("C2 price band combined (creation order kept)", st == 200 and got == [IDS["A"], IDS["B"]], f"{got}")

# ---- multi-tag (any-of) ----
st, got, res = ids_of("/search?tags=music,tech&q=c2suite")
check("C2 tags= any-of match", st == 200 and got == [IDS["A"], IDS["B"]], f"{got}")
check("C2 tags= no-match", ids_of("/search?tags=nonexistent&q=c2suite")[1] == [])
check("C2 tags echo as list", res.get("filters", {}).get("tags") == ["music", "tech"])

# ---- location substring ----
st, got, _ = ids_of("/search?location=ber&q=c2suite")
check("C2 location substring match", st == 200 and got == [IDS["A"], IDS["C"], IDS["D"]], f"{got}")
check("C2 location no-match", ids_of("/search?location=zzz&q=c2suite")[1] == [])

# ---- sort ----
st, got, _ = ids_of("/search?q=c2suite&sort=price")
check("C2 sort=price ascending", st == 200 and got == [IDS["C"], IDS["B"], IDS["A"], IDS["D"]], f"{got}")
st, got, _ = ids_of("/search?q=c2suite&sort=date")
check("C2 sort=date (dateless last)", st == 200 and got == [IDS["A"], IDS["B"], IDS["C"], IDS["D"]], f"{got}")
st, got, _ = ids_of("/search?q=c2suite&sort=newest")
check("C2 sort=newest (creation reversed)", st == 200 and got == [IDS["D"], IDS["C"], IDS["B"], IDS["A"]], f"{got}")

# ---- validation ----
st, res = req(HUB, "/search?q=c2suite&sort=bogus")
check("C2 invalid sort -> 400 honest error", st == 400 and "sort must be" in res.get("error", ""), f"{st} {res}")
st, res = req(HUB, "/search?from=2026/10/01")
check("C2 invalid from -> 400", st == 400 and "YYYY-MM-DD" in res.get("error", ""), f"{st} {res}")
st, res = req(HUB, "/search?min_price=abc")
check("C2 invalid min_price -> 400", st == 400 and "min_price must be a number" in res.get("error", ""), f"{st} {res}")

# ---- OpenAPI documents the new surface ----
st, api = req(HUB, "/openapi.json")
desc = str(api.get("paths", {}).get("/search", {}).get("get", {}).get("description", ""))
check("C2 OpenAPI /search documents from/to + tags + sort",
      st == 200 and "from/to" in desc and "tags=" in desc and "sort=date|price|newest" in desc, desc[:120])

# ---- chat natural-language parse path (real chatlib against the same hub) ----
import chatlib  # noqa: E402

r = chatlib.handle_text(HUB, "search under 8 c2suite", "c2-chat")
check("C2 chat 'under N' parses to max_price", "(under 8)" in r and "2 found" in r
      and chatlib._mb("Beta Workshop") in r and chatlib._mb("Gamma Meetup") in r, r[:120])

r = chatlib.handle_text(HUB, "search cheapest c2suite", "c2-chat")
check("C2 chat 'cheapest' sorts by price", "(cheapest first)" in r
      and r.find(chatlib._mb("Gamma Meetup")) < r.find(chatlib._mb("C2Suite Alpha")), r[:120])

r = chatlib.handle_text(HUB, "search soonest c2suite", "c2-chat")
check("C2 chat 'soonest' sorts by date", "(soonest first)" in r
      and r.find(chatlib._mb("Alpha Concert")) < r.find(chatlib._mb("Beta Workshop")), r[:120])

r = chatlib.handle_text(HUB, "search c2suite until 2026-11-30", "c2-chat")
check("C2 chat 'until DATE' parses to to=", "(until 2026-11-30)" in r and "2 found" in r
      and "Delta Repair" not in r, r[:120])

r = chatlib.handle_text(HUB, "search free c2suite", "c2-chat")
check("C2 chat 'free' still works alongside qualifiers", "(free only)" in r
      and "1 found" in r and chatlib._mb("Gamma Meetup") in r, r[:120])

r = chatlib.handle_text(HUB, "search over 6 c2suite", "c2-chat")
check("C2 chat 'over N' parses to min_price", "(over 6)" in r and "2 found" in r
      and chatlib._mb("Alpha") in r and chatlib._mb("Delta") in r, r[:120])

print(f"\n=== C2 search: {sum(1 for _, ok in RESULTS if ok)}/{len(RESULTS)} passed ===")
fails = [n for n, ok in RESULTS if not ok]
if fails:
    print("FAILED:", fails)
    sys.exit(1)
print("C2_SEARCH_PASSED")
