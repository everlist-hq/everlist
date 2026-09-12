"""Storage adapter (Plan 1.1): file (default, legacy-identical) or SQLite.

- file: exactly the legacy G1 semantics - 0600 temp file, fsync file + dir,
  atomic os.replace, HUB_FSYNC honored (benchmarking escape hatch).
- sqlite: the SAME snapshot JSON stored as one row (meta.snap) so the snapshot
  format stays the single source of truth; WAL + synchronous=FULL for durability
  parity, 0600 perms (the snapshot holds booking secrets - H17/M10 parity).
- Contract: read_snapshot() -> None = missing state (fresh start, both modes);
  a PRESENT-but-corrupt state RAISES so app.py's fail-closed exit-78 path is
  identical in both modes.
- app.py injects its RESOLVED paths via configure() (no guessed defaults here).
- H4 note: the single-instance flock stays on the canonical state-file path;
  in sqlite mode the DB lives alongside it (same operator config).
- B8 backups stay file-mode in 1.1: in sqlite mode _backup_locked no-ops
  naturally (no STATE_FILE yet) - the WAL'd DB file is the recovery surface.
"""
import json, os, sqlite3

FSYNC = os.environ.get("HUB_FSYNC", "1") != "0"
_cfg = {"mode": os.environ.get("HUB_STORAGE_MODE", "file").strip().lower(),
        "state_file": None, "db_file": None}

def configure(state_file, db_file=None):
    _cfg["state_file"] = state_file
    _cfg["db_file"] = db_file or os.path.join(
        os.path.dirname(os.path.abspath(state_file)), "hub.db")

def mode():
    return _cfg["mode"]

def _db():
    conn = sqlite3.connect(_cfg["db_file"], timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    try:
        os.chmod(_cfg["db_file"], 0o600)  # H17: snapshot holds booking secrets
    except OSError:
        pass
    for _suf in ("-wal", "-shm"):  # WAL sidecars hold the same data - same wall
        try:
            os.chmod(_cfg["db_file"] + _suf, 0o600)
        except OSError:
            pass
    return conn

def read_snapshot():
    if _cfg["mode"] == "sqlite":
        if not os.path.exists(_cfg["db_file"]):
            return None
        conn = _db()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            row = conn.execute("SELECT v FROM meta WHERE k='snap'").fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return json.loads(row[0])
    sf = _cfg["state_file"]
    if not os.path.exists(sf):
        return None
    with open(sf) as f:
        return json.load(f)

def write_snapshot(snap):
    if _cfg["mode"] == "sqlite":
        conn = _db()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('snap', ?)",
                         (json.dumps(snap),))
            conn.commit()
        finally:
            conn.close()
        return
    sf = _cfg["state_file"]
    tmp = sf + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(snap, f)
        if FSYNC:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, sf)
    if FSYNC:
        dfd = os.open(os.path.dirname(os.path.abspath(sf)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)