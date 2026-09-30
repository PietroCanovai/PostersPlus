"""Studio's own SQLite database: settings, the Jellyfin library index, and run history.

Kept apart from upstream's cache.db on purpose: that file is a cache (pruned,
rebuilt, reshaped by upstream), while this one holds the user's rules and
state, and is backed up as one file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any

DB_PATH = os.environ.get("STUDIO_DB_PATH", "/app/cache/studio.db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS items (
    jf_id          TEXT PRIMARY KEY,
    library_id     TEXT NOT NULL,
    library_name   TEXT NOT NULL,
    jf_type        TEXT NOT NULL,
    name           TEXT NOT NULL,
    year           INTEGER,
    tmdb_id        TEXT,
    imdb_id        TEXT,
    tvdb_id        TEXT,
    stage_show_id  TEXT,
    manual_tmdb_id TEXT,
    quality        TEXT NOT NULL DEFAULT '',
    jf_image_tag   TEXT,
    pushed_hash    TEXT,
    pushed_tag     TEXT,
    pushed_at      REAL,
    status         TEXT NOT NULL DEFAULT 'new',
    last_error     TEXT,
    revert_count   INTEGER NOT NULL DEFAULT 0,
    present        INTEGER NOT NULL DEFAULT 1,
    added_at       REAL NOT NULL,
    seen_at        REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS items_library ON items(library_id);
CREATE INDEX IF NOT EXISTS items_tmdb ON items(tmdb_id);
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  REAL NOT NULL,
    finished_at REAL,
    trigger     TEXT NOT NULL,
    scope       TEXT NOT NULL,
    dry_run     INTEGER NOT NULL,
    status      TEXT NOT NULL,
    counts      TEXT NOT NULL DEFAULT '{}',
    message     TEXT
);
CREATE TABLE IF NOT EXISTS run_items (
    run_id  INTEGER NOT NULL,
    jf_id   TEXT NOT NULL,
    name    TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT,
    at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS run_items_run ON run_items(run_id);
CREATE TABLE IF NOT EXISTS titles (
    title_key       TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    mode            TEXT NOT NULL DEFAULT 'auto',
    pinned_look_id  INTEGER,
    hands_off       INTEGER NOT NULL DEFAULT 0,
    style           TEXT NOT NULL DEFAULT '{}',
    deck            TEXT NOT NULL DEFAULT '[]',
    deck_pos        INTEGER NOT NULL DEFAULT 0,
    current_look_id INTEGER,
    rotated_on      TEXT,
    reviewed_at     REAL,
    updated_at      REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS looks (
    look_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    title_key   TEXT NOT NULL,
    poster      TEXT NOT NULL DEFAULT '',
    crop        TEXT NOT NULL DEFAULT '',
    own_title   INTEGER NOT NULL DEFAULT 0,
    logo        TEXT NOT NULL DEFAULT '',
    colors      TEXT NOT NULL DEFAULT '{}',
    style       TEXT NOT NULL DEFAULT '{}',
    in_rotation INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS looks_title ON looks(title_key);
CREATE TABLE IF NOT EXISTS uploads (
    title_key TEXT NOT NULL,
    kind      TEXT NOT NULL,
    path      TEXT NOT NULL,
    name      TEXT NOT NULL DEFAULT '',
    own_title INTEGER NOT NULL DEFAULT 0,
    added_at  REAL NOT NULL,
    PRIMARY KEY (title_key, path)
);
CREATE TABLE IF NOT EXISTS jf_art (
    title_key  TEXT NOT NULL,
    kind       TEXT NOT NULL,
    mode       TEXT NOT NULL,
    path       TEXT NOT NULL DEFAULT '',
    crop       TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL,
    PRIMARY KEY (title_key, kind)
);
CREATE TABLE IF NOT EXISTS frame_cache (
    src      TEXT PRIMARY KEY,   -- jf-chapter:<item>:<n>
    custom   TEXT NOT NULL,      -- its hidden custom: copy, what the renderer reads
    added_at REAL
);
CREATE TABLE IF NOT EXISTS item_images (
    jf_id       TEXT NOT NULL,
    kind        TEXT NOT NULL,
    pushed_hash TEXT,
    pushed_tag  TEXT,
    seen_tag    TEXT,
    pushed_at   REAL,
    PRIMARY KEY (jf_id, kind)
);
CREATE TABLE IF NOT EXISTS never (
    title_key TEXT NOT NULL,
    kind      TEXT NOT NULL,
    ref       TEXT NOT NULL,
    added_at  REAL NOT NULL,
    PRIMARY KEY (title_key, kind, ref)
);
"""

# Runs kept for the Activity page; older ones and their items are dropped.
MAX_RUNS = 60


def connect(path: str | None = None) -> sqlite3.Connection:
    """The shared connection, opened (and the schema created) on first use.
    *path* is for tests."""
    global _conn, DB_PATH
    with _lock:
        if path is not None and (_conn is None or path != DB_PATH):
            if _conn is not None:
                _conn.close()
                _conn = None
            DB_PATH = path
        if _conn is None:
            os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
            conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(_SCHEMA)
            _migrate(conn)
            _conn = conn
        return _conn


# Columns added after the first release: (table, column, declaration).
_ADDED_COLUMNS = (
    ("items", "parent_jf_id", "TEXT"),
    ("items", "season_number", "INTEGER"),
    ("items", "productions", "TEXT"),   # theatre: the show's productions (Jellyfin seasons), "|"-joined
    ("jf_art", "logo", "TEXT NOT NULL DEFAULT ''"),   # thumb: '' poster's, 'text', 'none', or a logo path
)


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, decl in _ADDED_COLUMNS:
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def execute(sql: str, args: tuple | list = ()) -> sqlite3.Cursor:
    with _lock:
        return connect().execute(sql, args)


def query(sql: str, args: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in connect().execute(sql, args).fetchall()]


def query_one(sql: str, args: tuple | list = ()) -> dict | None:
    with _lock:
        row = connect().execute(sql, args).fetchone()
        return dict(row) if row else None


# ── settings ────────────────────────────────────────────────────────────────

def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    if row is None:
        return default
    try:
        return json.loads(row["value"])
    except ValueError:
        return default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, json.dumps(value)),
    )


def delete_setting(key: str) -> None:
    execute("DELETE FROM settings WHERE key = ?", (key,))


# ── runs ────────────────────────────────────────────────────────────────────

def start_run(trigger: str, scope: str, dry_run: bool) -> int:
    cur = execute(
        "INSERT INTO runs (started_at, trigger, scope, dry_run, status) VALUES (?, ?, ?, ?, 'running')",
        (time.time(), trigger, scope, int(dry_run)),
    )
    return int(cur.lastrowid)


def log_run_item(run_id: int, jf_id: str, name: str, action: str, detail: str | None = None) -> None:
    execute(
        "INSERT INTO run_items (run_id, jf_id, name, action, detail, at) VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, jf_id, name, action, detail, time.time()),
    )


def finish_run(run_id: int, status: str, counts: dict, message: str | None = None) -> None:
    execute(
        "UPDATE runs SET finished_at = ?, status = ?, counts = ?, message = ? WHERE id = ?",
        (time.time(), status, json.dumps(counts), message, run_id),
    )
    with _lock:
        old = [r["id"] for r in query("SELECT id FROM runs ORDER BY id DESC LIMIT -1 OFFSET ?", (MAX_RUNS,))]
        for rid in old:
            execute("DELETE FROM run_items WHERE run_id = ?", (rid,))
            execute("DELETE FROM runs WHERE id = ?", (rid,))
