#!/usr/bin/env python
"""B3: state.json profiler — READ-ONLY diagnostic (backlog B3, data before B4).

Loads a real state.json as a template, synthesizes a worst-case snapshot at the
target scale (default 10k accounts / 5k listings, the backlog's numbers), and
times the EXACT production write path of app.py::_persist_locked (json.dump to
tmp + os.replace; since H3 the production path fsyncs file+dir by default —
pass --fsync to profile THAT path; default remains no-fsync for comparability
with the B3 baseline numbers). Plus the startup load.
All synthetic writes go to a temp dir; the real state file is only ever read.

Run: python tools/state_profile.py [path/to/state.json] [--accounts N] [--listings N]
Verdict: persist avg < 200ms -> json backend OK at this scale (B4 optional);
         otherwise B4 (SQLite adapter) becomes mandatory per the backlog gate.
"""
import argparse, copy, json, os, statistics, sys, tempfile, time

DEFAULT_ACCOUNTS = 10_000
DEFAULT_LISTINGS = 5_000
GATE_MS = 200.0  # backlog B4 gate


def synth(template, n_accounts, n_listings):
    """Worst-case-ish snapshot: replicate template entries to target scale."""
    snap = copy.deepcopy(template)
    accounts = snap.get("accounts", {}) or {}
    listings = snap.get("listings", []) or []
    bookings = snap.get("bookings", []) or []
    ledger = snap.get("ledger", []) or []

    a_proto = next(iter(accounts.values()), {
        "agent": "prof-agent", "kind": "code", "email": None, "email_verified": False,
        "pending_email": None, "pending_code_hash": None, "pending_exp": 0,
        "recovery_code_hash": None, "recovery_exp": 0, "gen": 0,
        "code_hash": "0" * 64, "created": 0.0})
    l_proto = listings[0] if listings else {
        "id": "even-1", "vertical": "events", "title": "Profile probe", "category": "meetup",
        "date": "2026-10-01", "price": 10, "location": "x", "capacity": 100,
        "registered": 0, "available": True, "owner": "acct-prof", "archived": False,
        "manage_code_hash": "0" * 64, "description": "d" * 200}
    b_proto = bookings[0] if bookings else {
        "id": "bk-prof1", "listing_id": "even-1", "escrow": "HELD", "amount": 10.0,
        "hub_fee": 0.1, "owner_payout": 9.9, "quantity": 1, "created": 0.0}
    g_proto = ledger[0] if ledger else {"ref": "bk-prof1", "type": "booking", "amount": 10.0}

    acc = dict(accounts)
    i = 0
    while len(acc) < n_accounts:
        acc[f"acct-prof{i:06d}"] = dict(a_proto)
        i += 1
    lst = list(listings)
    while len(lst) < n_listings:
        d = dict(l_proto)
        d["id"] = f"even-{len(lst) + 1}"
        d["title"] = f"Profile probe {len(lst) + 1}"
        lst.append(d)
    bk = list(bookings) + [dict(b_proto) for _ in range(max(0, n_listings // 2 - len(bookings)))]
    lg = list(ledger) + [dict(g_proto) for _ in range(max(0, n_listings // 2 - len(ledger)))]

    snap["accounts"] = acc
    snap["listings"] = lst
    snap["bookings"] = bk
    snap["ledger"] = lg
    snap.setdefault("id_counters", {})["even"] = max(snap.get("id_counters", {}).get("even", 0), n_listings)
    return snap


def bytes_per(snap):
    out = {}
    for k, v in snap.items():
        try:
            out[k] = len(json.dumps(v))
        except Exception:
            out[k] = -1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("state", nargs="?", default=os.path.join(os.path.dirname(__file__), "..", ".run", "state.json"))
    ap.add_argument("--accounts", type=int, default=DEFAULT_ACCOUNTS)
    ap.add_argument("--listings", type=int, default=DEFAULT_LISTINGS)
    ap.add_argument("--iters", type=int, default=8)
    ap.add_argument("--fsync", action="store_true",
                    help="profile the production durability path (app.py fsyncs file+dir since H3, HUB_FSYNC=1 default)")
    args = ap.parse_args()

    with open(args.state) as f:
        template = json.load(f)
    print(f"real state: {args.state} ({os.path.getsize(args.state)} bytes)")
    print("collections:", {k: (len(v) if isinstance(v, (list, dict)) else v) for k, v in template.items()})

    snap = synth(template, args.accounts, args.listings)
    sizes = bytes_per(snap)
    total = sum(v for v in sizes.values() if v > 0)
    print(f"\nsynthetic worst-case: {args.accounts} accounts / {args.listings} listings / "
          f"{len(snap.get('bookings', []))} bookings / {len(snap.get('ledger', []))} ledger")
    print("bytes per collection:", sizes)
    print(f"total json size ~ {total} bytes ({total / 1e6:.1f} MB)")

    tmpdir = tempfile.mkdtemp(prefix="state-profile-")
    dst = os.path.join(tmpdir, "state.json")
    times = []
    for _ in range(args.iters):
        t0 = time.perf_counter()
        tmp = dst + ".tmp"
        with open(tmp, "w") as f:
            json.dump(snap, f)
            if args.fsync:
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, dst)
        if args.fsync:
            dfd = os.open(tmpdir, os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        times.append((time.perf_counter() - t0) * 1000)
    print(f"\npersist ({'fsync file+dir (production path)' if args.fsync else 'no fsync'}), {args.iters} iters: "
          f"min {min(times):.1f}ms / avg {statistics.mean(times):.1f}ms / max {max(times):.1f}ms")

    t0 = time.perf_counter()
    with open(dst) as f:
        json.load(f)
    load_ms = (time.perf_counter() - t0) * 1000
    print(f"startup load (json.load): {load_ms:.1f}ms")

    avg = statistics.mean(times)
    print(f"\nVERDICT: persist avg {avg:.1f}ms vs gate {GATE_MS:.0f}ms -> "
          f"{'JSON OK at this scale (B4 optional)' if avg < GATE_MS else 'B4 SQLite adapter MANDATORY per backlog gate'}")
    print("note: profiled path was "
          + ("fsync file+dir (matches app.py production since H3)" if args.fsync
             else "NO fsync (baseline; app.py DOES fsync by default since H3, HUB_FSYNC=0 disables)")
          + " - durability on power-cut is guaranteed only on the fsync path.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
