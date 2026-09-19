#!/usr/bin/env python3
"""H2: restore an EverList state backup (the drill that makes B8 backups real).

A backup that was never restored is decoration. This tool closes the loop:

  restore_backup.py --list              show available backups (newest last)
  restore_backup.py --latest            restore the newest backup -> state.json
  restore_backup.py --file N            restore the Nth backup (0 = oldest)

Safety:
- Refuses to run while a hub is LIVE on the same state (flock probe on
  <state>.lock, same discipline the hub itself will use for H4).
- The CURRENT state.json is saved to state.pre-restore-<ns>.json first — a
  restore can itself be undone.
- Atomic replace after verification (JSON parses + has the expected keys).
- sqlite mode (HUB_STORAGE_MODE=sqlite): restores write meta.snap into
  hub.db via the storage adapter; the undo point is the current DB snapshot.

Usage: python tools/restore_backup.py [--state PATH] (--list | --latest | --file N)
State path defaults to HUB_STATE_FILE or ./state.json (backups live in <dir>/backups/).
"""
import argparse
import fcntl
import json
import os
import shutil
import sys
import time

REQUIRED_KEYS = {"listings", "bookings", "ledger"}


def backup_dir(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), "backups")


def list_backups(state_path):
    bdir = backup_dir(state_path)
    if not os.path.isdir(bdir):
        return []
    return sorted(f for f in os.listdir(bdir)
                  if f.startswith("state-") and f.endswith(".json"))


def verify_snapshot(path):
    """A restore candidate must parse and carry the core state keys."""
    with open(path, "r", encoding="utf-8") as fh:
        snap = json.load(fh)
    missing = REQUIRED_KEYS - set(snap)
    if missing:
        raise ValueError(f"not a valid state snapshot (missing {sorted(missing)}): {path}")
    return snap


def hub_is_live(state_path):
    """Best-effort probe: if a hub holds the state lockfile, refuse."""
    lock_path = os.path.abspath(state_path) + ".lock"
    if not os.path.exists(lock_path):
        return False
    try:
        with open(lock_path, "a+") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(fh, fcntl.LOCK_UN)
                return False
            except OSError:
                return True
    except OSError:
        return False


def restore(state_path, backup_name, dry_run=False):
    state_path = os.path.abspath(state_path)
    src = os.path.join(backup_dir(state_path), backup_name)
    if not os.path.exists(src):
        raise SystemExit(f"backup not found: {src}")
    snap = verify_snapshot(src)
    stats = {k: len(snap.get(k) or []) for k in sorted(REQUIRED_KEYS)}
    print(f"backup : {backup_name}")
    print(f"source : {src}")
    print(f"stats  : {stats}")
    if dry_run:
        # --check is read-only: allowed even while a live hub holds the lock
        # (H4) — operators may preview a restore before stopping the hub.
        print("dry-run: no changes made (--check)")
        return
    if hub_is_live(state_path):
        raise SystemExit("REFUSING: a live hub appears to hold this state lock. "
                         "Stop it first (make down).")
    if os.environ.get("HUB_STORAGE_MODE", "file").strip().lower() == "sqlite":
        # B8 parity: in sqlite mode meta.snap inside hub.db is the single
        # source of truth - restore INTO the DB through the storage adapter
        # (also rebuilds the derived C6 index in the same transaction).
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import storage
        db_file = os.path.join(os.path.dirname(state_path), "hub.db")
        storage.configure(state_path, db_file)
        cur = storage.read_snapshot()
        if cur is not None:
            undo = os.path.join(os.path.dirname(state_path),
                                f"state.pre-restore-{time.time_ns()}.json")
            fd = os.open(undo, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cur, fh)
            print(f"undo   : current DB snapshot saved -> {os.path.basename(undo)}")
        storage.write_snapshot(snap)
        print(f"restored -> {db_file} (meta.snap; sqlite mode)")
        print("start the hub and verify with: curl <hub>/listings | count + /ledger totals")
        return
    # keep the current state as an undo point
    if os.path.exists(state_path):
        undo = os.path.join(os.path.dirname(state_path),
                            f"state.pre-restore-{time.time_ns()}.json")
        shutil.copy2(state_path, undo)
        print(f"undo   : current state saved -> {os.path.basename(undo)}")
    tmp = state_path + ".restore-tmp"
    # 0600 from creation: restored state.json carries booking secrets +
    # account hashes; never let umask decide (storage.py discipline).
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(snap, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, state_path)
    os.chmod(state_path, 0o600)
    dfd = os.open(os.path.dirname(os.path.abspath(state_path)) or ".", os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    print(f"restored -> {state_path}")
    print("start the hub and verify with: curl <hub>/listings | count + /ledger totals")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=os.environ.get("HUB_STATE_FILE", "state.json"))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--latest", action="store_true")
    g.add_argument("--file", type=int, metavar="N", help="0 = oldest backup")
    ap.add_argument("--check", action="store_true", help="show what would be restored, change nothing")
    a = ap.parse_args()
    baks = list_backups(a.state)
    if a.list:
        if not baks:
            print("no backups found in", backup_dir(a.state))
            return
        print(f"{len(baks)} backup(s) in {backup_dir(a.state)} (0 = oldest):")
        for i, b in enumerate(baks):
            print(f"  [{i}] {b}")
        return
    if not baks:
        raise SystemExit("no backups found — nothing to restore")
    if a.latest:
        restore(a.state, baks[-1], dry_run=a.check)
    else:
        if not (-len(baks) <= a.file < len(baks)):
            raise SystemExit(f"--file {a.file} out of range (have {len(baks)} backups, 0..{len(baks)-1})")
        restore(a.state, baks[a.file], dry_run=a.check)


if __name__ == "__main__":
    main()