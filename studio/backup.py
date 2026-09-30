"""Backups of everything you decided in Studio: per-title rules (modes, looks,
Never lists, styles) and the global style.  Secrets are never included.

Written automatically after each nightly run to /app/cache/studio-backups/
(the last KEEP days), and downloadable / restorable from Settings.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date

from . import db

VERSION = 1
KEEP = 14
# Settings worth carrying over; secrets (API keys, session secret) never are.
_SETTINGS = ("style_applied", "style_draft", "style_previous", "libraries", "schedule_time", "resolution",
             "seasons_enabled")


def backup_dir() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "studio-backups")
    os.makedirs(path, exist_ok=True)
    return path


def export() -> dict:
    return {
        "studio_backup": VERSION,
        "exported_at": time.time(),
        "titles": db.query("SELECT * FROM titles"),
        "looks": db.query("SELECT * FROM looks"),
        "never": db.query("SELECT * FROM never"),
        "uploads": db.query("SELECT * FROM uploads"),
        "settings": {k: db.get_setting(k) for k in _SETTINGS if db.get_setting(k) is not None},
    }


def restore(data: dict) -> dict:
    """Replace every rule with the backup's.  Look ids are renumbered, and the
    titles' references to them (pinned look, rotation deck) follow."""
    if not isinstance(data, dict) or data.get("studio_backup") != VERSION:
        raise ValueError("That isn't a Studio backup")
    titles, looks, never = data.get("titles") or [], data.get("looks") or [], data.get("never") or []
    if not all(isinstance(x, list) for x in (titles, looks, never)):
        raise ValueError("That backup is damaged")
    with db._lock:
        conn = db.connect()
        conn.execute("BEGIN")
        try:
            for table in ("looks", "never", "titles") + (("uploads",) if "uploads" in data else ()):
                conn.execute(f"DELETE FROM {table}")
            for u in data.get("uploads") or []:
                conn.execute("INSERT OR IGNORE INTO uploads (title_key, kind, path, name, own_title, added_at) "
                             "VALUES (?, ?, ?, ?, ?, ?)",
                             (str(u["title_key"]), str(u["kind"]), str(u["path"]), u.get("name") or "",
                              int(bool(u.get("own_title"))), float(u.get("added_at") or time.time())))
            new_ids: dict[int, int] = {}
            for lk in looks:
                cur = conn.execute(
                    "INSERT INTO looks (title_key, poster, crop, own_title, logo, colors, style, in_rotation, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(lk["title_key"]), lk.get("poster") or "", lk.get("crop") or "", int(bool(lk.get("own_title"))),
                     lk.get("logo") or "", lk.get("colors") or "{}", lk.get("style") or "{}",
                     int(bool(lk.get("in_rotation"))), float(lk.get("created_at") or time.time())))
                new_ids[int(lk["look_id"])] = int(cur.lastrowid)
            for t in titles:
                deck = [new_ids[i] for i in json.loads(t.get("deck") or "[]") if i in new_ids]
                conn.execute(
                    "INSERT INTO titles (title_key, name, mode, pinned_look_id, hands_off, style, deck, deck_pos, "
                    "current_look_id, rotated_on, reviewed_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (str(t["title_key"]), t.get("name") or "", t.get("mode") or "auto",
                     new_ids.get(t.get("pinned_look_id")), int(bool(t.get("hands_off"))), t.get("style") or "{}",
                     json.dumps(deck), 0, new_ids.get(t.get("current_look_id")), t.get("rotated_on"),
                     t.get("reviewed_at"), float(t.get("updated_at") or time.time())))
            for n in never:
                conn.execute("INSERT OR IGNORE INTO never (title_key, kind, ref, added_at) VALUES (?, ?, ?, ?)",
                             (str(n["title_key"]), str(n["kind"]), str(n["ref"]), float(n.get("added_at") or time.time())))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
    for k, v in (data.get("settings") or {}).items():
        if k in _SETTINGS:
            db.set_setting(k, v)
    return {"titles": len(titles), "looks": len(looks), "never": len(never)}


def write_daily(today: date | None = None) -> str:
    """Today's backup file (overwritten within a day), older than KEEP days removed."""
    today = today or date.today()
    folder = backup_dir()
    path = os.path.join(folder, f"studio-{today.isoformat()}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(export(), fh, indent=1)
    os.replace(tmp, path)
    files = sorted(f for f in os.listdir(folder) if f.startswith("studio-") and f.endswith(".json"))
    for old in files[:-KEEP]:
        try:
            os.remove(os.path.join(folder, old))
        except OSError:
            pass
    return path
