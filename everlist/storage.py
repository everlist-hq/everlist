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
- B8 backups (2026-09-18): BOTH modes rotate pre-persist snapshots to
  <state_dir>/backups/state-<ns>.json (plain JSON, identical restore tooling);
  in sqlite mode the CURRENT meta.snap is dumped before each overwrite.
- C6 (vertical expansion plan 1.2): sqlite mode additionally maintains a
  DERIVED, rebuildable listing index - normalized columns (vertical, city,
  date, tags) + an FTS5 external-content index over title/description/tags -
  updated in the SAME transaction as meta.snap. meta.snap remains the single
  source of truth; the index is a pure cache (safe to drop/rebuild anytime).
"""
import json, os, re, sqlite3, time

FSYNC = os.environ.get("HUB_FSYNC", "1") != "0"
_cfg = {"mode": os.environ.get("HUB_STORAGE_MODE", "file").strip().lower(),
        "state_file": None, "db_file": None}

def configure(state_file, db_file=None):
    _cfg["state_file"] = state_file
    _cfg["db_file"] = db_file or os.path.join(
        os.path.dirname(os.path.abspath(state_file)), "hub.db")

def mode():
    return _cfg["mode"]

# --- C6: derived listing index (sqlite mode only) ---------------------------
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS listings (
        id TEXT PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'offer',
        vertical TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        date TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        description TEXT NOT NULL DEFAULT '')""",
    "CREATE INDEX IF NOT EXISTS idx_listings_role ON listings(role)",
    "CREATE INDEX IF NOT EXISTS idx_listings_vertical ON listings(vertical)",
    "CREATE INDEX IF NOT EXISTS idx_listings_city ON listings(city)",
    "CREATE INDEX IF NOT EXISTS idx_listings_date ON listings(date)",
    "CREATE INDEX IF NOT EXISTS idx_listings_tags ON listings(tags)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS listings_fts USING fts5(
        title, description, tags, content='listings', content_rowid='rowid')""",
    "DROP TRIGGER IF EXISTS listings_ai",
    "DROP TRIGGER IF EXISTS listings_ad",
    "DROP TRIGGER IF EXISTS listings_au",
]

def _listing_values(l):
    role = l.get("role") or "offer"
    loc = l.get("location")
    if isinstance(loc, dict):
        city = loc.get("city") or ""
    elif isinstance(loc, str):
        city = loc
    else:
        city = ""
    tags = " ".join(str(t) for t in (l.get("tags") or []))
    return (role, l.get("vertical") or "", city, l.get("date") or "", tags,
            l.get("title") or "", l.get("description") or "")

def _sync_index_conn(conn, snap):
    """Sync listings table + listings_fts full-text index.
    Explicitly rebuilds listings_fts to ensure rowid alignment (robust against trigger failures).
    """
    for stmt in _SCHEMA:
        conn.execute(stmt)
    raw = (snap or {}).get("listings") or {}
    if isinstance(raw, dict):
        listings_data = [(lid, l) for lid, l in raw.items()]
    else:
        listings_data = [(l.get("id"), l) for l in raw]
    # Clear both tables
    conn.execute("DELETE FROM listings_fts")
    conn.execute("DELETE FROM listings")
    # Insert fresh data with proper alignment
    for lid, l in listings_data:
        values = _listing_values(l)
        row = (lid,) + values
        cur = conn.execute(
            "INSERT INTO listings(id, role, vertical, city, date, tags, title, description) "
            "VALUES(?,?,?,?,?,?,?,?)", row)
        # explicit FTS row keyed by the REAL rowid (external-content table:
        # columns are title, description, tags; rowid links back to listings)
        conn.execute(
            "INSERT INTO listings_fts(rowid, title, description, tags) VALUES(?,?,?,?)",
            (cur.lastrowid, values[5], values[6], values[4]))
# ---------------------------------------------------------------------------

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

# B8 parity: same env knobs as app.py's file-mode rotation (shared operator config).
BACKUP_MIN_BYTES = int(os.environ.get("HUB_BACKUP_MIN_BYTES", "1000000"))
BACKUP_KEEP = int(os.environ.get("HUB_BACKUP_KEEP", "5"))


def _backup_snap_locked():
    """B8 parity for sqlite mode: called at the START of a sqlite write_snapshot,
    BEFORE the incoming snapshot overwrites meta.snap - dumps the CURRENT
    meta.snap to <state_dir>/backups/state-<ns>.json (plain JSON so
    tools/restore_backup.py and the H2 drill work unchanged in both modes) and
    keeps the newest BACKUP_KEEP. Caller wraps in try/except: backup failure
    must NEVER break persistence (same discipline as file mode)."""
    if not _cfg.get("state_file"):
        return
    cur = read_snapshot()
    if cur is None:
        return
    blob = json.dumps(cur)
    if len(blob.encode()) < BACKUP_MIN_BYTES:  # small dev states stay clean
        return
    bdir = os.path.join(os.path.dirname(os.path.abspath(_cfg["state_file"])), "backups")
    os.makedirs(bdir, exist_ok=True)
    try:
        os.chmod(bdir, 0o700)  # H17: backups hold the same secrets
    except OSError:
        pass
    bpath = os.path.join(bdir, f"state-{time.time_ns()}.json")
    fd = os.open(bpath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # H17: 0600 from creation
    with os.fdopen(fd, "w") as f:
        f.write(blob)
        f.flush()
        os.fsync(f.fileno())
    baks = sorted(f for f in os.listdir(bdir)
                  if f.startswith("state-") and f.endswith(".json"))
    for old_b in baks[:-BACKUP_KEEP]:
        try:
            os.remove(os.path.join(bdir, old_b))
        except OSError:
            pass


def write_snapshot(snap):
    if _cfg["mode"] == "sqlite":
        # B8 parity (2026-09-18): sqlite mode previously kept NO pre-persist
        # backups while deploy.sh production sets HUB_STORAGE_MODE=sqlite -
        # mirror the file-mode B8 contract here; never break the write.
        try:
            _backup_snap_locked()
        except Exception as ex:
            print(f"[BACKUP] warning: sqlite rotation failed ({ex})", flush=True)
        conn = _db()
        try:
            conn.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('snap', ?)",
                         (json.dumps(snap),))
            _sync_index_conn(conn, snap)  # C6: derived index, SAME transaction
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

def search_ids(query):
    """Search listings via FTS5 (sqlite mode). Returns list of listing IDs.

    Sanitizes query (word chars only) to avoid FTS5 syntax errors.
    Returns None if FTS5 is unavailable (fallback to full scan), [] if no matches.
    """
    if not query:
        return None
    # Sanitize: word chars only + normalize spaces
    q = re.sub(r"\W+", " ", query).strip().lower()
    if not q:
        return None
    conn = _db()
    try:
        rowids = [r[0] for r in conn.execute(
            "SELECT rowid FROM listings_fts WHERE listings_fts MATCH ?",
            (q,)).fetchall()]
        if not rowids:
            return []
        marks = ",".join("?" * len(rowids))
        rows = conn.execute(
            f"SELECT id FROM listings WHERE rowid IN ({marks})",
            rowids).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        # Index not ready (fresh db: FTS tables exist only after the first
        # persisted snapshot). None = "index unavailable" -> caller falls
        # back to the legacy substring scan; [] stays a genuine no-match.
        return None
    finally:
        conn.close()

def search_listings(conn, query):
    """Search listings via FTS5 (sqlite mode). Returns list of listing IDs.

    Performs a MATCH query against listings_fts, joined back to listings
    to return the IDs. If FTS5 table doesn't exist (fresh/db issue),
    returns empty list (caller falls back to JSON filtering if needed).
    """
    if not query:
        return []
    try:
        rows = conn.execute(
            "SELECT l.id FROM listings l "
            "JOIN listings_fts f ON l.rowid = f.rowid "
            "WHERE f MATCH ?", (query,)).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        # FTS5 table might not exist yet (e.g. fresh db) -> no matches from FTS
        return []
