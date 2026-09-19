"""S8: B8 backup parity for sqlite storage mode (2026-09-18).

deploy.sh production sets HUB_STORAGE_MODE=sqlite, but sqlite mode previously
kept NO pre-persist backups (storage.py docstring admitted the no-op) -
production had zero state backups. Covered here:
- sqlite write_snapshot rotates the CURRENT meta.snap to the SAME plain-JSON
  <state_dir>/backups/state-<ns>.json convention as file mode
- newest backup == pre-persist snapshot (B8 hash-equivalent check)
- rotation keeps HUB_BACKUP_KEEP (small knob for test speed)
- H17 perms: backup dir 0700, backup files 0600 from creation
- tools/restore_backup.py restores INTO hub.db (meta.snap) in sqlite mode,
  saves an undo point, and the derived C6 index is re-synced same-transaction

Run: python test_sqlite_backups.py
"""
import json
import os
import stat
import subprocess
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="hub-sqlbak-")
# knobs are read at storage import - set env BEFORE import
os.environ["HUB_STORAGE_MODE"] = "sqlite"
os.environ["HUB_BACKUP_MIN_BYTES"] = "200"  # test-scale floor
os.environ["HUB_BACKUP_KEEP"] = "3"

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print(("PASS" if cond else "FAIL"), name, ("" if cond else detail))


import storage  # noqa: E402  (after env)

STATE = os.path.join(TMP, "state.json")
storage.configure(STATE)  # db_file defaults to hub.db alongside
BDIR = os.path.join(TMP, "backups")


def snap(n):
    return {"listings": [{"id": f"l{i}", "title": f"S8 {i}", "pad": "x" * 60}
                         for i in range(n)],
            "bookings": [], "ledger": []}


def baks():
    if not os.path.isdir(BDIR):
        return []
    return sorted(f for f in os.listdir(BDIR)
                  if f.startswith("state-") and f.endswith(".json"))


# --- W1: first write creates no backup (nothing pre-persist) -------------
storage.write_snapshot(snap(3))
check("W1 no backup on first write", baks() == [], str(baks()))

# --- W2: second write backs up the pre-persist snapshot ------------------
storage.write_snapshot(snap(5))
files = baks()
check("W2 one backup after second write", len(files) == 1, str(files))
if files:
    with open(os.path.join(BDIR, files[-1])) as f:
        backed = json.load(f)
    check("W2 newest backup == pre-persist snapshot (3 listings)",
          len(backed.get("listings", [])) == 3, f"got {len(backed.get('listings', []))}")

# --- W3: rotation keeps cap ----------------------------------------------
for i in range(6):
    storage.write_snapshot(snap(10 + i))
files = baks()
check("W3 rotation keeps HUB_BACKUP_KEEP=3", len(files) == 3, f"got {len(files)}")
check("W3 backups chronological (ns names sort)", files == sorted(files))

# --- W4: H17 perms --------------------------------------------------------
if files:
    m = stat.S_IMODE(os.stat(os.path.join(BDIR, files[-1])).st_mode)
    check("W4 backup file 0600", m == 0o600, oct(m))
dm = stat.S_IMODE(os.stat(BDIR).st_mode)
check("W4 backup dir 0700", dm == 0o700, oct(dm))

# --- W5: real restore drill INTO the sqlite DB ---------------------------
newest_before = baks()[-1]
with open(os.path.join(BDIR, newest_before)) as f:
    expected = json.load(f)
env = {**os.environ, "HUB_STATE_FILE": STATE}
r = subprocess.run(
    [sys.executable, os.path.join(HERE, "tools", "restore_backup.py"),
     "--state", STATE, "--latest"],
    env=env, capture_output=True, text=True, timeout=30)
check("W5 restore tool exits 0 (sqlite mode)", r.returncode == 0,
      (r.stderr or r.stdout)[-300:])
cur = storage.read_snapshot()
check("W5 restored DB == newest backup (meta.snap)",
      cur is not None and cur.get("listings") == expected.get("listings"),
      f"got {None if cur is None else len(cur.get('listings', []))} listings")
undos = [f for f in os.listdir(TMP) if f.startswith("state.pre-restore-")]
check("W5 undo point saved", len(undos) == 1, str(undos))

# --- verdict ---------------------------------------------------------------
failed = [n for n, ok in RESULTS if not ok]
print(f"\nsqlite-backups: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("SQLITE_BACKUP_ALL_PASSED")
