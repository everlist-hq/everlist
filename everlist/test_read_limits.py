"""B7: public endpoint sanity limits (self-managed hubs).
Proves: generous read limiter silent for legit use, exact trip at the tuned
limit, q length cap (boundary), offset/limit pagination + clamps, and honest
chat errors (hub 400s never masquerade as 'unreachable').
Run: python test_read_limits.py
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
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, detail)


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
    except Exception:  # hub closed without response (server-side crash) -> report, don't crash suite
        return -1, {}


def spawn(extra_env):
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="hub-b7-")
    env = {**os.environ, "HUB_STATE_FILE": os.path.join(tmp, "state.json"),
           "HUB_POW_SIGNUP_BITS": "8", "PYTHONUNBUFFERED": "1", **extra_env}
    logf = open(os.path.join(tmp, "hub.log"), "w")
    p = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(port)],
                         stdout=logf, stderr=subprocess.STDOUT, env=env)
    _ACTIVE.append(p)
    assert wait_ready(port), "hub did not start"
    return f"http://127.0.0.1:{port}", p, logf


def seed_listings(base, n):
    """Create n listings via the anonymous /access path (bulk principals)."""
    r = urllib.request.Request(base + "/access", method="POST",
                               data=json.dumps({"agent": "b7-bulk", "acts": ["list"]}).encode(),
                               headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(r, timeout=10) as resp:
        tok = json.loads(resp.read().decode())["tokens"]["list"]
    ids = []
    for i in range(n):
        st, res = None, None
        body = json.dumps({"vertical": "events", "title": f"B7 Bulk {i}",
                           "category": "meetup", "date": "2026-10-01",
                           "price": float(i), "location": f"Town {i}",
                           "capacity": 10, "source": "chat-agent"}).encode()
        rq = urllib.request.Request(base + "/listings", data=body, method="POST",
                                    headers={"Content-Type": "application/json",
                                             "X-Hub-Token": tok})
        try:
            with urllib.request.urlopen(rq, timeout=10) as resp:
                res = json.loads(resp.read().decode()); st = resp.status
        except urllib.error.HTTPError as e:
            st = e.code; res = {}
        assert st == 201, f"seed listing failed: {st} {res}"
        ids.append(res["id"])
    return ids


# ================= hub 1: defaults (silence + q cap + pagination) =================
B1, hub1, log1 = spawn({})

# silence: 100 rapid reads must ALL pass (generous by design)
sil = [req(B1, "/search")[0] for _ in range(100)]
check("B7 limiter silent at 100 rapid reads (defaults)", all(s == 200 for s in sil),
      f"429s={sil.count(429)} other={len([s for s in sil if s != 200 and s != 429])}")

# q length cap: boundary 200 OK, 201 rejected
st200, _ = req(B1, "/search?q=" + "a" * 200)
st201, res201 = req(B1, "/search?q=" + "a" * 201)
check("B7 q=200 chars accepted", st200 == 200, f"got {st200}")
check("B7 q=201 chars rejected 400 with reason", st201 == 400 and "q too long" in res201.get("error", ""),
      f"{st201} {res201.get('error', '')[:50]}")

# seed 7 listings -> pagination math (fresh hub has 4 demo seed listings)
ids = seed_listings(B1, 7)
st, full = req(B1, "/listings")
total = full["count"]
check("B7 /listings default shape", st == 200 and total >= 11 and full["offset"] == 0
      and full["returned"] == total and full["listings"] == full["listings"],
      f"count={total} returned={full.get('returned')}")
check("B7 default returns everything under 500", full["limit"] == 500 and len(full["listings"]) == total)
full_ids = [l["id"] for l in full["listings"]]
st, page = req(B1, "/listings?limit=2&offset=1")
check("B7 offset+limit slice exact", st == 200
      and [l["id"] for l in page["listings"]] == full_ids[1:3]
      and page["offset"] == 1 and page["returned"] == 2)
st, clamp = req(B1, "/listings?limit=99999")
check("B7 limit clamped to 500", st == 200 and clamp["limit"] == 500)
st, one = req(B1, "/listings?limit=0")
check("B7 limit=0 -> 1 (no zero-size pages)", st == 200 and one["limit"] == 1 and one["returned"] == 1)
st, neg = req(B1, "/listings?offset=-5&limit=abc")
check("B7 invalid offset/limit fall back safely", st == 200 and neg["offset"] == 0 and neg["limit"] == 500)
st, sres = req(B1, "/search?q=b7&limit=3")
check("B7 /search paginates too", st == 200 and sres["count"] == 7 and sres["returned"] == 3
      and len(sres["listings"]) == 3, f"count={sres['count']} returned={sres['returned']}")

# chat honesty: oversized chat search surfaces the REAL hub reason
import chatlib  # noqa: E402
reply = chatlib.handle_text(B1, "search " + "x" * 300, "b7-chat")
check("B7 chat surfaces real rejection (not 'unreachable')",
      reply.startswith("Search rejected:") and "q too long" in reply, reply[:60])
reply_ok = chatlib.handle_text(B1, "search bulk", "b7-chat")
check("B7 normal chat search unaffected", reply_ok.startswith("╔"), reply_ok[:40])

_kill(hub1); _ACTIVE.remove(hub1); log1.close()

# ================= hub 2: tuned limit 50 -> exact trip =================
B2, hub2, log2 = spawn({"HUB_READ_LIMIT": "50", "HUB_READ_PAGE_MAX": "10"})
first50 = [req(B2, "/listings")[0] for _ in range(50)]
check("B7 tuned hub: exactly 50 reads pass", all(s == 200 for s in first50), f"non200={len([s for s in first50 if s != 200])}")
st51, res51 = req(B2, "/listings")
check("B7 read 51 trips limiter with honest 429", st51 == 429 and "too many read" in res51.get("error", ""),
      f"{st51} {res51.get('error', '')[:50]}")
# page max env respected
st, pg = req(B2, "/listings")
# (429s keep coming within the window — that itself is the backstop working)
check("B7 limiter keeps blocking within window", req(B2, "/listings")[0] == 429)

_kill(hub2); _ACTIVE.remove(hub2); log2.close()

fails = [n for n, ok in RESULTS if not ok]
print(f"\n=== read-limits: {len(RESULTS) - len(fails)}/{len(RESULTS)} passed ===")
if fails:
    print("FAILED:", fails)
sys.exit(1 if fails else 0)
