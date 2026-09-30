"""Per-title rules and the resolver that turns them into render parameters.

A *title* is what rules belong to: every Jellyfin item with the same TMDB id
(a 4K and a 1080p copy, say) shares one.  A title is in one of three modes:

  auto      PostersPlus picks, minus anything marked Never
  pinned    one look, always
  rotation  the looks marked "in rotation", one per day, shuffled with no
            repeats until every look has had its day

A *look* is a poster plus everything that goes with it: its own logo, crop,
colours and style tweaks.  An empty poster or logo means "the automatic pick".
"""
from __future__ import annotations

import json
import random
import re
import time
from dataclasses import dataclass, field
from datetime import date

from . import db

MODES = ("auto", "pinned", "rotation")
NEVER_KINDS = ("poster", "logo")
# Look colours → the renderer parameters they set.
COLOR_PARAMS = {"tint": "tint_color", "fade": "fade_color", "sash_text": "notch_text_color",
                "rating_text": "rating_text_color", "logo": "logo_color"}
_HEX = re.compile(r"^[0-9a-fA-F]{6}$")
_PARAM = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# A style override may set any render parameter except these: identity and
# keys (never stored), and the art parameters, which looks own.  (Colours are
# allowed: a title in automatic mode keeps its colours in its style.)
_NOT_STYLE = {"tmdb_id", "imdb_id", "type", "stremio_id", "anilist_id", "kitsu_id", "quality",
              "access_key", "tmdb_key", "mdblist_key", "fanart_key", "tvdb_key", "season", "episode",
              "primary_client", "resolution", "nocache", "debug", "shape"}


def title_key(row: dict) -> str:
    tmdb_id = row.get("manual_tmdb_id") or row.get("tmdb_id")
    if tmdb_id and row.get("jf_type") == "Season":
        return f"tmdb:tv:{tmdb_id}:s{int(row.get('season_number') or 0)}"
    if tmdb_id:
        return f"tmdb:{'tv' if row.get('jf_type') == 'Series' else 'movie'}:{tmdb_id}"
    if row.get("stage_show_id") and not row.get("imdb_id"):
        return f"stage:{row['stage_show_id']}"   # every recording of a show shares its rules
    return f"jf:{row['jf_id']}"


# ── Validation ──────────────────────────────────────────────────────────────

def clean_style(style) -> dict:
    if not isinstance(style, dict):
        raise ValueError("style must be an object")
    out = {}
    for k, v in style.items():
        k = str(k)
        if k in _NOT_STYLE or k.startswith("art_") or not _PARAM.match(k):
            raise ValueError(f"{k} can't be set per title")
        if v is None or v == "":
            continue
        v = str(v)
        if len(v) > 500:
            raise ValueError(f"{k} is too long")
        out[k] = v
    return out


def clean_colors(colors) -> dict:
    if not isinstance(colors, dict):
        raise ValueError("colors must be an object")
    out = {}
    for k, v in colors.items():
        if k == "logo_mode":
            if v in ("solid", "tint"):
                out[k] = v
            continue
        if k not in COLOR_PARAMS:
            raise ValueError(f"Unknown colour {k}")
        if v in (None, ""):
            continue
        v = str(v).lstrip("#")
        if not _HEX.match(v):
            raise ValueError(f"{k} must be a colour like #1a2b3c")
        out[k] = v.lower()
    return out


def clean_crop(crop) -> str:
    if crop in (None, "", {}):
        return ""
    if isinstance(crop, dict):
        x, y, z = float(crop.get("x", 0.5)), float(crop.get("y", 0.5)), float(crop.get("zoom", 1))
    else:
        x, y, z = (float(p) for p in str(crop).split(","))
    if not (0 <= x <= 1 and 0 <= y <= 1 and 1 <= z <= 4):
        raise ValueError("crop out of range")
    return f"{x:.4f},{y:.4f},{z:.3f}"


# ── Storage ─────────────────────────────────────────────────────────────────

def _decode_title(row: dict | None, key: str) -> dict:
    if row is None:
        return {"title_key": key, "name": "", "mode": "auto", "pinned_look_id": None, "hands_off": 0,
                "style": {}, "deck": [], "deck_pos": 0, "current_look_id": None, "rotated_on": None,
                "reviewed_at": None, "updated_at": None}
    row = dict(row)
    row["style"] = json.loads(row["style"] or "{}")
    row["deck"] = json.loads(row["deck"] or "[]")
    return row


def _decode_look(row: dict) -> dict:
    row = dict(row)
    row["colors"] = json.loads(row["colors"] or "{}")
    row["style"] = json.loads(row["style"] or "{}")
    row["in_rotation"] = bool(row["in_rotation"])
    row["own_title"] = bool(row["own_title"])
    return row


def get_title(key: str) -> dict:
    return _decode_title(db.query_one("SELECT * FROM titles WHERE title_key = ?", (key,)), key)


def ensure_title(key: str, name: str = "") -> dict:
    db.execute("INSERT INTO titles (title_key, name, updated_at) VALUES (?, ?, ?) "
               "ON CONFLICT(title_key) DO UPDATE SET name = CASE WHEN excluded.name != '' "
               "THEN excluded.name ELSE titles.name END", (key, name, time.time()))
    return get_title(key)


def _touch(key: str, **fields) -> None:
    ensure_title(key)
    fields["updated_at"] = time.time()
    sets = ", ".join(f"{k} = ?" for k in fields)
    vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
    db.execute(f"UPDATE titles SET {sets} WHERE title_key = ?", (*vals, key))


def looks(key: str) -> list[dict]:
    return [_decode_look(r) for r in db.query("SELECT * FROM looks WHERE title_key = ? ORDER BY look_id", (key,))]


def get_look(look_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM looks WHERE look_id = ?", (look_id,))
    return _decode_look(row) if row else None


def never(key: str) -> dict[str, set]:
    out = {k: set() for k in NEVER_KINDS}
    for r in db.query("SELECT kind, ref FROM never WHERE title_key = ?", (key,)):
        out.setdefault(r["kind"], set()).add(r["ref"])
    return out


def set_mode(key: str, mode: str, pinned_look_id: int | None = None) -> None:
    if mode not in MODES:
        raise ValueError("mode must be auto, pinned or rotation")
    if mode == "pinned":
        look = get_look(pinned_look_id) if pinned_look_id else None
        if not look or look["title_key"] != key:
            raise ValueError("pin which look?")
    _touch(key, mode=mode, pinned_look_id=pinned_look_id if mode == "pinned" else None)


def set_hands_off(key: str, on: bool) -> None:
    _touch(key, hands_off=int(bool(on)))


def set_title_style(key: str, style: dict) -> None:
    _touch(key, style=clean_style(style))


def mark_reviewed(key: str, on: bool = True) -> None:
    _touch(key, reviewed_at=time.time() if on else None)


def _look_fields(fields: dict) -> dict:
    out = {}
    if "poster" in fields:
        out["poster"] = str(fields["poster"] or "")
    if "crop" in fields:
        out["crop"] = clean_crop(fields["crop"])
    if "own_title" in fields:
        out["own_title"] = int(bool(fields["own_title"]))
    if "logo" in fields:
        out["logo"] = str(fields["logo"] or "")
    if "colors" in fields:
        out["colors"] = json.dumps(clean_colors(fields["colors"]))
    if "style" in fields:
        out["style"] = json.dumps(clean_style(fields["style"]))
    if "in_rotation" in fields:
        out["in_rotation"] = int(bool(fields["in_rotation"]))
    return out


def add_look(key: str, fields: dict, validate_path=None) -> dict:
    """validate_path(path) raises ValueError for a path the renderer won't accept."""
    ensure_title(key)
    f = _look_fields(fields)
    for p in (f.get("poster"), f.get("logo")):
        if p and p != "text" and validate_path:
            validate_path(p)
    cols = ["title_key", "created_at", *f]
    cur = db.execute(f"INSERT INTO looks ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                     (key, time.time(), *f.values()))
    _touch(key)
    return get_look(int(cur.lastrowid))


def update_look(look_id: int, fields: dict, validate_path=None) -> dict:
    look = get_look(look_id)
    if not look:
        raise KeyError("No such look")
    f = _look_fields(fields)
    for p in (f.get("poster"), f.get("logo")):
        if p and p != "text" and validate_path:
            validate_path(p)
    if f:
        db.execute(f"UPDATE looks SET {', '.join(f'{k} = ?' for k in f)} WHERE look_id = ?", (*f.values(), look_id))
    _touch(look["title_key"])
    return get_look(look_id)


def delete_look(look_id: int) -> None:
    look = get_look(look_id)
    if not look:
        return
    db.execute("DELETE FROM looks WHERE look_id = ?", (look_id,))
    t = get_title(look["title_key"])
    if t["pinned_look_id"] == look_id:
        _touch(look["title_key"], mode="auto", pinned_look_id=None)
    else:
        _touch(look["title_key"])


def set_never(key: str, kind: str, ref: str, on: bool) -> None:
    if kind not in NEVER_KINDS:
        raise ValueError("kind must be poster or logo")
    ensure_title(key)
    if on:
        db.execute("INSERT OR IGNORE INTO never (title_key, kind, ref, added_at) VALUES (?, ?, ?, ?)",
                   (key, kind, ref, time.time()))
        # A poster that is never to be used can't be in the rotation either.
        if kind == "poster":
            db.execute("UPDATE looks SET in_rotation = 0 WHERE title_key = ? AND poster = ?", (key, ref))
    else:
        db.execute("DELETE FROM never WHERE title_key = ? AND kind = ? AND ref = ?", (key, kind, ref))
    _touch(key)


def reset_title(key: str) -> None:
    db.execute("DELETE FROM looks WHERE title_key = ?", (key,))
    db.execute("DELETE FROM never WHERE title_key = ?", (key,))
    db.execute("DELETE FROM titles WHERE title_key = ?", (key,))


def summaries() -> dict[str, dict]:
    """Per title: mode, hands off, rotation size, never count, style/colour
    customised — for the library grid's chips."""
    out: dict[str, dict] = {}
    for t in db.query("SELECT title_key, mode, hands_off, style, reviewed_at FROM titles"):
        out[t["title_key"]] = {"mode": t["mode"], "hands_off": bool(t["hands_off"]),
                               "styled": t["style"] not in ("{}", ""), "reviewed": bool(t["reviewed_at"]),
                               "rotation": 0, "never": 0}
    for r in db.query("SELECT title_key, SUM(in_rotation) AS n, "
                      "SUM(CASE WHEN colors != '{}' OR style != '{}' THEN 1 ELSE 0 END) AS styled "
                      "FROM looks GROUP BY title_key"):
        s = out.setdefault(r["title_key"], {"mode": "auto", "hands_off": False, "styled": False,
                                            "reviewed": False, "rotation": 0, "never": 0})
        s["rotation"] = int(r["n"] or 0)
        s["styled"] = s["styled"] or bool(r["styled"])
    for r in db.query("SELECT title_key, COUNT(*) AS n FROM never GROUP BY title_key"):
        s = out.setdefault(r["title_key"], {"mode": "auto", "hands_off": False, "styled": False,
                                            "reviewed": False, "rotation": 0, "never": 0})
        s["never"] = int(r["n"])
    return out


# ── Resolver ────────────────────────────────────────────────────────────────

@dataclass
class Resolution:
    skip: bool = False
    reason: str = ""
    look_id: int | None = None
    params: dict = field(default_factory=dict)
    upcoming: list = field(default_factory=list)   # rotation: the next looks, in order


def _shuffled(ids: list[int], avoid_first: int | None, rng: random.Random) -> list[int]:
    deck = list(ids)
    rng.shuffle(deck)
    if len(deck) > 1 and deck[0] == avoid_first:
        swap = rng.randrange(1, len(deck))
        deck[0], deck[swap] = deck[swap], deck[0]
    return deck


def _rotation_pick(t: dict, pool: list[int], today: str, advance: bool, rng: random.Random) -> tuple[int, list[int]]:
    """Today's look from a shuffled deck that runs through the whole pool before
    any look repeats.  The deck only moves on a new day and only when *advance*
    (the nightly run) says so; previews and manual pushes show today's."""
    deck, pos, current = list(t["deck"]), int(t["deck_pos"] or 0), t["current_look_id"]
    changed = False
    if sorted(deck) != sorted(pool) or not (0 <= pos < len(deck)):
        # New pool (a look added, removed or marked Never): a fresh deck,
        # not opening on the look showing now.
        deck, pos, changed = _shuffled(pool, current, rng), 0, True
    elif advance and t["rotated_on"] not in (None, today):
        pos += 1
        if pos >= len(deck):
            deck, pos = _shuffled(pool, deck[-1], rng), 0
        changed = True
    look_id = deck[pos]
    if advance or changed:
        fields = {"deck": deck, "deck_pos": pos, "current_look_id": look_id}
        if advance:
            fields["rotated_on"] = today
        _touch(t["title_key"], **fields)
    return look_id, deck[pos + 1:]


def look_params(look: dict) -> dict:
    p: dict = {}
    if look.get("poster"):
        p["art_poster"] = look["poster"]
        if look.get("crop"):
            p["art_crop"] = look["crop"]
        if look.get("own_title"):
            p["art_original"] = "1"
    if look.get("logo"):
        p["art_logo"] = look["logo"]
    colors = look.get("colors") or {}
    for k, param in COLOR_PARAMS.items():
        if colors.get(k):
            p[param] = colors[k]
    if colors.get("logo") and colors.get("logo_mode") == "tint":
        p["logo_color_mode"] = "tint"
    p.update(look.get("style") or {})
    return p


def resolve(key: str, *, today: date | None = None, advance: bool = False,
            rng: random.Random | None = None, look_override: dict | None = None,
            title_style: dict | None = None) -> Resolution:
    """The render parameters for a title today.  *look_override* renders a look
    that isn't saved yet, *title_style* an unsaved title style (the editor's
    live preview)."""
    t = get_title(key)
    if t["hands_off"] and look_override is None:
        return Resolution(skip=True, reason="Hands off")
    nev = never(key)
    all_looks = {l["look_id"]: l for l in looks(key)}
    look, upcoming = None, []
    if look_override is not None:
        look = look_override
    elif t["mode"] == "pinned" and t["pinned_look_id"] in all_looks:
        look = all_looks[t["pinned_look_id"]]
        if look["poster"] and look["poster"] in nev["poster"]:
            look = None
    elif t["mode"] == "rotation":
        pool = [lid for lid, l in all_looks.items()
                if l["in_rotation"] and not (l["poster"] and l["poster"] in nev["poster"])]
        if pool:
            lid, rest = _rotation_pick(t, pool, (today or date.today()).isoformat(), advance,
                                       rng or random.Random())
            look, upcoming = all_looks[lid], rest
    params = dict(t["style"] if title_style is None else title_style)
    if look is not None:
        params.update(look_params(look))
    if nev["poster"] and "art_poster" not in params:
        params["art_exclude"] = ",".join(sorted(nev["poster"]))
    if nev["logo"] and "art_logo" not in params:
        params["art_logo_exclude"] = ",".join(sorted(nev["logo"]))
    return Resolution(look_id=look.get("look_id") if look else None, params=params, upcoming=upcoming)
