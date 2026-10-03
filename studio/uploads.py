"""Your own images, per title: every upload or pasted link is kept here,
whether or not a look uses it, until you delete it.

Files are PostersPlus's custom art (art_overrides.store_custom_image, stored
under CUSTOM_ART_DIR as "custom:<hash>.jpg|png"), so the renderer draws them
like any Artwork-tab image.  Kinds:
  poster    2:3 art (own_title: it already carries the title, so no logo)
  backdrop  wide art, framed to 2:3 when used
  logo      a transparent PNG
"""
from __future__ import annotations

import time

from . import db

KINDS = ("poster", "backdrop", "logo")
# How art_overrides stores each kind (sizes it may shrink to).
STORE_KIND = {"poster": "poster", "backdrop": "landscape", "logo": "logo"}


def add(title_key: str, kind: str, path: str, name: str = "") -> dict:
    if kind not in KINDS:
        raise ValueError("kind must be poster, backdrop or logo")
    db.execute("INSERT INTO uploads (title_key, kind, path, name, added_at) VALUES (?, ?, ?, ?, ?) "
               "ON CONFLICT(title_key, path) DO UPDATE SET kind = excluded.kind, "
               "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE uploads.name END",
               (title_key, kind, path, (name or "")[:120], time.time()))
    return get(title_key, path)


def get(title_key: str, path: str) -> dict | None:
    row = db.query_one("SELECT * FROM uploads WHERE title_key = ? AND path = ?", (title_key, path))
    if row:
        row["own_title"] = bool(row["own_title"])
    return row


def for_title(title_key: str) -> list[dict]:
    """This title's uploads, newest first, plus any custom image a look uses
    that predates the uploads table."""
    rows = db.query("SELECT * FROM uploads WHERE title_key = ? ORDER BY added_at DESC", (title_key,))
    for r in rows:
        r["own_title"] = bool(r["own_title"])
    known = {r["path"] for r in rows}
    for lk in db.query("SELECT poster, logo, crop, own_title FROM looks WHERE title_key = ?", (title_key,)):
        for path, kind in ((lk["poster"], "backdrop" if lk["crop"] else "poster"), (lk["logo"], "logo")):
            if path and path.startswith("custom:") and path not in known:
                known.add(path)
                rows.append({"title_key": title_key, "kind": kind, "path": path, "name": "",
                             "own_title": bool(lk["own_title"]) if kind == "poster" else False, "added_at": 0})
    return rows


def set_own_title(title_key: str, path: str, on: bool) -> None:
    db.execute("UPDATE uploads SET own_title = ? WHERE title_key = ? AND path = ?", (int(bool(on)), title_key, path))
    # Looks already using it follow.
    db.execute("UPDATE looks SET own_title = ? WHERE title_key = ? AND poster = ?", (int(bool(on)), title_key, path))


def remove(title_key: str, path: str) -> bool:
    """Forget an upload; the file goes too when nothing else uses it.  Looks
    using it keep working until you change them (the file stays then)."""
    db.execute("DELETE FROM uploads WHERE title_key = ? AND path = ?", (title_key, path))
    if in_use(path):
        return False
    try:
        import os

        import art_overrides
        file = art_overrides.custom_file(path)
        if file and os.path.exists(file):
            os.remove(file)
        return True
    except Exception:
        return False


def in_use(path: str) -> bool:
    """Whether a Studio upload or look, or an Artwork-tab override, still uses a custom image."""
    if db.query_one("SELECT 1 FROM uploads WHERE path = ?", (path,)):
        return True
    if db.query_one("SELECT 1 FROM looks WHERE poster = ? OR logo = ?", (path, path)):
        return True
    if path in _art_paths():
        return True
    try:
        from cache import get_db
        return get_db().execute("SELECT 1 FROM art_overrides WHERE path = ?", (path,)).fetchone() is not None
    except Exception:
        return True   # can't tell: keep the file


def _art_paths() -> set[str]:
    """The images pinned, or in a rotation, as a title's Backdrop / Logo / Thumb."""
    import json
    paths: set[str] = set()
    for r in db.query("SELECT path, logo, pool FROM jf_art"):
        paths.update(p for p in (r["path"], r["logo"]) if p)
        try:
            paths.update(p.get("path") or "" for p in json.loads(r.get("pool") or "[]"))
        except (ValueError, AttributeError):
            pass
    paths.discard("")
    return paths


def studio_custom_paths() -> set[str]:
    """Every custom image Studio uses (for art_overrides' clean-up, which
    otherwise only knows the Artwork tab's own choices)."""
    paths = {r["path"] for r in db.query("SELECT path FROM uploads WHERE path LIKE 'custom:%'")}
    paths |= {r["custom"] for r in db.query("SELECT custom FROM frame_cache")}
    paths |= {p for p in _art_paths() if p.startswith("custom:")}
    for r in db.query("SELECT poster, logo FROM looks"):
        for p in (r["poster"], r["logo"]):
            if p and p.startswith("custom:"):
                paths.add(p)
    return paths
