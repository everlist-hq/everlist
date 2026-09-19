"""MCP face regression suite (owner-approved Phase D, 2026-09-17).
The MCP endpoint (/mcp, JSON-RPC 2.0) is a THIN adapter: every tool call
forwards to this hub's own REST contract over loopback, so all hub laws
(auth, rate limits, validation, escrow) apply unchanged. This locks the
contract: handshake + version negotiation, session header, tool surface,
honest error mapping, batch, and a REAL booking E2E through MCP itself.
Run: python test_mcp.py
"""
import sys
import hashlib, json, os, socket, subprocess, sys, time, atexit, tempfile
import urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
PASS, FAIL = [], []


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"
STATE = os.path.join(tempfile.mkdtemp(prefix="hub-mcp-"), "state.json")
env = {**os.environ, "HUB_STATE_FILE": STATE}
proc = subprocess.Popen([sys.executable, os.path.join(HERE, "app.py"), str(PORT)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, env=env)

atexit.register(lambda: proc.terminate())


def req(method, path, payload=None, tok=None):
    r = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={"Content-Type": "application/json", **({"X-Hub-Token": tok} if tok else {})},
        method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def mcp(payload, tok=None):
    headers = {"Content-Type": "application/json"}
    if tok:
        headers["X-Hub-Token"] = tok
    r = urllib.request.Request(BASE + "/mcp", data=json.dumps(payload).encode(),
                               headers=headers, method="POST")
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, dict(resp.headers), json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), json.loads(e.read() or b"null")


def wait_ready(timeout=15):
    dl = time.time() + timeout
    while time.time() < dl:
        try:
            req("GET", "/.well-known/agent-hub.json"); return True
        except Exception:
            time.sleep(0.2)
    return False


def solve_pow(kind):
    _, ch = req("GET", f"/auth/challenge?kind={kind}")
    n = 0
    while True:
        d = hashlib.sha256((ch["challenge"] + str(n)).encode()).digest()
        bits = 0
        for b in d:
            if b == 0: bits += 8; continue
            bits += 8 - b.bit_length(); break
        if bits >= ch["difficulty"]:
            return {"challenge": ch["challenge"], "nonce": n}
        n += 1


def check(name, cond, detail=""):
    cond = bool(cond)
    (PASS if cond else FAIL).append(name)
    print(("PASS " if cond else "FAIL ") + name + (" -- " + str(detail)[:200] if detail and not cond else ""))


def result_text(resp):
    return resp["result"]["content"][0]["text"]


assert wait_ready(), "hub did not start"
print("== MCP face (Phase D: JSON-RPC 2.0 adapter over the REST contract) ==")

# --- handshake ---
st, hdrs, init = mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "mcp-suite", "version": "0"}}})
check("initialize 200", st == 200, st)
check("protocol negotiated", init.get("result", {}).get("protocolVersion") == "2024-11-05", init)
check("serverInfo name", init.get("result", {}).get("serverInfo", {}).get("name") == "everlist-hub", init)
check("session header issued", any(k.lower() == "mcp-session-id" for k in hdrs), hdrs)

st, _, body = mcp({"jsonrpc": "2.0", "method": "notifications/initialized"})
check("notification accepted (202)", st == 202, (st, body))

# --- tool surface ---
st, _, tl = mcp({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
names = [t["name"] for t in tl.get("result", {}).get("tools", [])]
check("six tools advertised", len(names) == 6 and set(names) == {
    "everlist_contract", "everlist_verticals", "everlist_search",
    "everlist_listing", "everlist_book", "everlist_booking"}, names)

st, _, call = mcp({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                   "params": {"name": "everlist_contract", "arguments": {}}})
contract = json.loads(result_text(call)) if st == 200 else {}
check("contract via MCP", contract.get("protocol") == "agent-hub/0.2", call)

st, _, call = mcp({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                   "params": {"name": "everlist_verticals", "arguments": {}}})
verts = json.loads(result_text(call)).get("verticals", {})
check("verticals via MCP (jobs present)", "jobs" in verts, list(verts))

st, _, call = mcp({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                   "params": {"name": "everlist_search", "arguments": {"q": "jazz"}}})
search = json.loads(result_text(call))
check("search via MCP", search.get("count", 0) >= 1, search)

seed_id = (search.get("listings") or [{}])[0].get("id", "")
st, _, call = mcp({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                   "params": {"name": "everlist_listing", "arguments": {"id": seed_id}}})
listing = json.loads(result_text(call)) if st == 200 else {}
check("listing via MCP", listing.get("id") == seed_id, call)

st, _, call = mcp({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                   "params": {"name": "everlist_listing", "arguments": {"id": "bogus-9"}}})
check("unknown listing = honest isError", call.get("result", {}).get("isError") is True
      and "no such listing" in result_text(call), call)

st, _, call = mcp({"jsonrpc": "2.0", "id": 8, "method": "tools/call",
                   "params": {"name": "everlist_book", "arguments": {"listing_id": "even-1"}}})
check("book without token = honest isError", call.get("result", {}).get("isError") is True
      and "missing hub token" in result_text(call), call)

# --- accounts via REST (auth stays a hub law; MCP never mints) ---
c, a = req("POST", "/accounts/signup", {"agent": "mcp-org", "pow": solve_pow("signup")})
c, l = req("POST", "/accounts/login", {"account_code": a["account_code"], "agent": "mcp-org"})
ltok = l["tokens"]["list"]
c, a2 = req("POST", "/accounts/signup", {"agent": "mcp-buyer", "pow": solve_pow("signup")})
c, l2 = req("POST", "/accounts/login", {"account_code": a2["account_code"], "agent": "mcp-buyer"})
btok = l2["tokens"]["book"]

c, li = req("POST", "/listings", {"title": "MCP E2E rooftop jazz", "date": "2026-10-05",
    "location": "Berlin", "price": 25, "capacity": 10, "vertical": "events",
    "category": "concert"}, tok=ltok)
check("organizer listing created via REST", c == 201, (c, li))
lid = li["id"]

# --- THE E2E: a real booking made THROUGH the MCP face ---
st, _, call = mcp({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                   "params": {"name": "everlist_book", "arguments": {
                       "listing_id": lid, "quantity": 1, "human_verified": True,
                       "attendee": "Ana Attendee"}}}, tok=btok)
booking = json.loads(result_text(call)) if st == 200 else {}
check("booking E2E via MCP", booking.get("escrow") in ("HELD", "WAIVED"), call)
bid = booking.get("id", "")

st, _, call = mcp({"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                   "params": {"name": "everlist_booking", "arguments": {
                       "booking_id": bid}}}, tok=btok)
bstat = json.loads(result_text(call)) if st == 200 else {}
check("booking status via MCP", bstat.get("escrow") in ("HELD", "WAIVED"), call)

# --- protocol errors ---
st, _, call = mcp({"jsonrpc": "2.0", "id": 11, "method": "resources/list"})
check("unknown method = -32601", call.get("error", {}).get("code") == -32601, call)

st, _, call = mcp({"hello": "world"})
check("non-JSON-RPC = -32600", call.get("error", {}).get("code") == -32600, call)

st, _, batch = mcp([{"jsonrpc": "2.0", "id": 21, "method": "ping"},
                    {"jsonrpc": "2.0", "id": 22, "method": "ping"}])
check("batch of two pings", isinstance(batch, list) and len(batch) == 2
      and batch[0].get("id") == 21 and batch[1].get("id") == 22, batch)

print(f"\nmcp suite: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
sys.exit(0)
