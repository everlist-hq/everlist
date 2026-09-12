"""Task 1.1: storage adapter tests — file mode legacy parity + sqlite mode parity.

Per the storage contract (storage.py docstring):
- missing state -> read_snapshot() is None (fresh start), BOTH modes
- present-but-corrupt state -> RAISES (app.py turns this into fail-closed exit 78), BOTH modes
- file mode: legacy G1 semantics (0600 state, no tmp leftovers, atomic replace)
- sqlite mode: same snapshot JSON, WAL + synchronous=FULL, 0600 db (H17/M10 parity)
"""
import json
import os
import sqlite3
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import storage


def _perms(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _snap():
    return {"listings": [{"id": "evt-1", "price": 10.0}],
            "bookings": [{"id": "bk-1", "listing": "evt-1"}],
            "ledger": [{"type": "book", "t": 1}],
            "idempotency": {"k1": {"h": "x", "r": {"s": 1}}},
            "accounts": {"acct-1": {"gen": 2, "code_hash": "ab"}},
            "id_counters": {"events": 5},
            "nonces": {"n1": 1}, "x402_nonces": {}, "settlements": {}}


def _roundtrip(mode):
    with tempfile.TemporaryDirectory() as td:
        sf = os.path.join(td, "state.json")
        storage._cfg["mode"] = mode
        storage.configure(sf)
        assert storage.read_snapshot() is None, f"{mode}: missing state must read None"
        snap = _snap()
        storage.write_snapshot(snap)
        got = storage.read_snapshot()
        assert got == snap, f"{mode}: round-trip mismatch: {got}"
        # a second write must overwrite cleanly (idempotent replace, not append)
        snap["listings"].append({"id": "evt-2", "price": 5.0})
        storage.write_snapshot(snap)
        assert storage.read_snapshot() == snap, f"{mode}: second write mismatch"
        if mode == "file":
            assert _perms(sf) == 0o600, f"file: state not 0600: {oct(_perms(sf))}"
            assert not os.path.exists(sf + ".tmp"), "file: tmp file left behind"
        else:
            dbf = storage._cfg["db_file"]
            assert os.path.exists(dbf), "sqlite: db missing"
            assert _perms(dbf) == 0o600, f"sqlite: db not 0600: {oct(_perms(dbf))}"
            for suf in ("-wal", "-shm"):  # sidecars only exist while WAL is live
                p = dbf + suf
                if os.path.exists(p):
                    assert _perms(p) == 0o600, f"sqlite: {suf} not 0600"


def _corrupt_raises(mode):
    """The fail-closed contract: corrupt state RAISES, never silently returns {}."""
    with tempfile.TemporaryDirectory() as td:
        sf = os.path.join(td, "state.json")
        storage._cfg["mode"] = mode
        storage.configure(sf)
        if mode == "file":
            with open(sf, "w") as f:
                f.write("{corrupt json")
        else:
            storage.write_snapshot({"ok": True})
            conn = sqlite3.connect(storage._cfg["db_file"])
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('snap', ?)", ("{corrupt",))
            conn.commit()
            conn.close()
        try:
            storage.read_snapshot()
        except Exception:
            return
        raise AssertionError(f"{mode}: corrupt state must RAISE (fail-closed), not return")


def _legacy_file_identical():
    """File mode must produce a JSON file the LEGACY loader could read (parity)."""
    with tempfile.TemporaryDirectory() as td:
        sf = os.path.join(td, "state.json")
        storage._cfg["mode"] = "file"
        storage.configure(sf)
        storage.write_snapshot(_snap())
        with open(sf) as f:
            assert json.load(f)["listings"][0]["id"] == "evt-1"


if __name__ == "__main__":
    for m in ("file", "sqlite"):
        _roundtrip(m)
        _corrupt_raises(m)
        print(f"  {m}: round-trip OK, overwrite OK, fail-closed OK, perms OK")
    _legacy_file_identical()
    print("  file: legacy-readable JSON parity OK")
    print("PASS: storage adapter (file+sqlite round-trip, fail-closed, 0600 perms)")