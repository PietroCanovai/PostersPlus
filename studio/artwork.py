"""Jellyfin's other images: Backdrop, Logo and Thumb.

Unlike the Primary poster these aren't composed from a style (except the
Thumb's "landscape" source): Studio picks an image and sends it as it is.

Per title and kind: automatic, pinned (a path, optionally framed), a daily
rotation of images you chose (backdrops), or keep (leave Jellyfin's).
Automatic follows the library rules in Settings:
  backdrop  the providers' backgrounds of exactly the rule's size (default
            1920x1080), textless first if asked
  logo      PostersPlus's own pick in the style's language order (/logo)
  thumb     PostersPlus's landscape render (logo + style over a backdrop),
            or the best backdrop with the title on it
A kind is only managed once switched on in Settings.

What you pinned or put in a rotation is yours: Studio sends exactly that and
puts it back when Jellyfin (or a plugin) replaces it.  With "resize" on, a
backdrop of yours that isn't the rule's size is cropped and resized to it.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import random
import re
import time
from datetime import date
from urllib.parse import parse_qsl, urlencode

import httpx

from . import db, prefs, rules

logger = logging.getLogger("studio")

KINDS = ("backdrop", "logo", "thumb")
JF_TYPE = {"backdrop": "Backdrop", "logo": "Logo", "thumb": "Thumb"}
MODES = ("auto", "pinned", "rotation", "keep")
ROTATING = ("backdrop",)   # the kinds that can rotate
ASPECT = {"backdrop": 16 / 9, "thumb": 16 / 9}

DEFAULT_RULES = {
    "backdrop": {"enabled": False, "min_w": 1920, "min_h": 1080, "wide_only": True, "textless": True,
                 "resize": False},
    "logo": {"enabled": False},
    "thumb": {"enabled": False, "source": "landscape"},
}


def library_rules() -> dict:
    saved = db.get_setting("jf_art") or {}
    return {k: {**DEFAULT_RULES[k], **(saved.get(k) or {})} for k in KINDS}


def set_library_rules(new: dict) -> dict:
    cur = library_rules()
    for kind, vals in (new or {}).items():
        if kind not in KINDS or not isinstance(vals, dict):
            continue
        for k, v in vals.items():
            if k not in DEFAULT_RULES[kind]:
                continue
            if isinstance(DEFAULT_RULES[kind][k], bool):
                cur[kind][k] = bool(v)
            elif isinstance(DEFAULT_RULES[kind][k], int):
                cur[kind][k] = max(0, min(10000, int(v)))
            elif k == "source" and v in ("landscape", "backdrop"):
                cur[kind][k] = v
    db.set_setting("jf_art", cur)
    return cur


FRAME_RE = re.compile(r"^jf-chapter:[0-9a-f]{32}:\d{1,3}$")


def is_frame(path) -> bool:
    return isinstance(path, str) and bool(FRAME_RE.match(path))


# IMDb's art on Cinemeta's Metahub CDN: offered by studio.candidates, but not a
# host the renderer downloads from, so it is used through a copy like a frame.
REMOTE_RE = re.compile(r"^https://images\.metahub\.space/(poster|background|logo)/(small|medium|large)/tt\d{1,10}/img$")


def is_remote(path) -> bool:
    return isinstance(path, str) and bool(REMOTE_RE.match(path))


async def realize_path(path: str, crop: str = "", aspect: float = 16 / 9) -> str:
    """A path the renderer can read: frames and IMDb's images (and, framed
    with *crop*, any image) become a hidden custom: copy, made the first time
    it's used and kept out of the title's own images."""
    from . import stage
    if not (is_frame(path) or is_remote(path) or crop or stage.is_stage_host(path)):
        return path
    import art_overrides
    src = f"{path}#{crop}#{aspect:.4f}" if crop else path
    row = db.query_one("SELECT custom FROM frame_cache WHERE src = ?", (src,))
    if row and art_overrides.custom_art_bytes(row["custom"]) is not None:
        return row["custom"]
    data = await fetch(path)
    if crop:
        data, _ = _frame(data, crop, aspect)
    remote = REMOTE_RE.match(path) if not crop else None
    kind = {"poster": "poster", "logo": "logo"}.get(remote.group(1), "landscape") if remote else "landscape"
    custom = await asyncio.to_thread(art_overrides.store_custom_image, data, kind=kind)
    db.execute("INSERT OR REPLACE INTO frame_cache (src, custom, added_at) VALUES (?, ?, ?)", (src, custom, time.time()))
    return custom


async def realize(params: dict) -> dict:
    """Render parameters with any frame or IMDb image swapped for its renderable copy."""
    out = params
    for k in ("art_poster", "art_logo"):
        if is_frame(params.get(k)) or is_remote(params.get(k)):
            out = {**out, k: await realize_path(params[k])}
    return out


# ── Per-title choices ───────────────────────────────────────────────────────

def choice(title_key: str, kind: str) -> dict:
    row = db.query_one("SELECT * FROM jf_art WHERE title_key = ? AND kind = ?", (title_key, kind))
    if not row:
        return {"title_key": title_key, "kind": kind, "mode": "auto", "path": "", "crop": "", "logo": "",
                "pool": [], "deck": [], "deck_pos": 0, "current": None, "rotated_on": None}
    row["pool"] = json.loads(row.get("pool") or "[]")
    row["deck"] = json.loads(row.get("deck") or "[]")
    return row


def set_choice(title_key: str, kind: str, mode: str, path: str = "", crop: str = "") -> dict:
    if kind not in KINDS or mode not in MODES:
        raise ValueError("bad kind or mode")
    if mode == "pinned" and not path:
        raise ValueError("pin which image?")
    old = choice(title_key, kind)
    if mode == "rotation" and (kind not in ROTATING or not old["pool"]):
        raise ValueError("Add images to the rotation first (the ↻ button)")
    if mode == "auto" and not path and not old.get("logo") and not old["pool"]:
        db.execute("DELETE FROM jf_art WHERE title_key = ? AND kind = ?", (title_key, kind))
    else:
        db.execute("INSERT INTO jf_art (title_key, kind, mode, path, crop, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
                   "ON CONFLICT(title_key, kind) DO UPDATE SET mode=excluded.mode, path=excluded.path, "
                   "crop=excluded.crop, updated_at=excluded.updated_at",
                   (title_key, kind, mode, path if mode == "pinned" else "", crop if mode == "pinned" else "",
                    time.time()))
    return choice(title_key, kind)


def managed(title_key: str, kind: str) -> bool:
    """Whether Studio sends this image for this title."""
    c = choice(title_key, kind)
    if c["mode"] == "keep":
        return False
    return c["mode"] in ("pinned", "rotation") or library_rules()[kind]["enabled"]


def set_logo(title_key: str, kind: str, logo: str) -> dict:
    """The generated thumb's logo: '' the poster's, 'text', 'none' (the art
    has its title), or a logo path.  Keeps the image choice."""
    c = choice(title_key, kind)
    db.execute("INSERT INTO jf_art (title_key, kind, mode, path, crop, logo, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(title_key, kind) DO UPDATE SET logo=excluded.logo, updated_at=excluded.updated_at",
               (title_key, kind, c["mode"], c.get("path") or "", c.get("crop") or "", logo, time.time()))
    return choice(title_key, kind)


# ── Rotation: images of yours, one a day ────────────────────────────────────

def rotate(title_key: str, kind: str, path: str, crop: str = "", on: bool = True) -> dict:
    """Put an image in the title's daily rotation, or take it out.  The first
    one added switches the title to rotation (a pinned image comes along);
    taking the last one out goes back to automatic."""
    if kind not in ROTATING:
        raise ValueError(f"A {kind} can't rotate")
    if not path:
        raise ValueError("rotate which image?")
    c = choice(title_key, kind)
    pool = [p for p in c["pool"] if p["path"] != path]
    if on:
        if not pool and c["mode"] == "pinned" and c.get("path") and c["path"] != path:
            pool.append({"path": c["path"], "crop": c.get("crop") or ""})
        pool.append({"path": path, "crop": crop or ""})
    mode = "rotation" if pool else ("auto" if c["mode"] == "rotation" else c["mode"])
    keep = mode == "pinned"
    db.execute("INSERT INTO jf_art (title_key, kind, mode, path, crop, logo, pool, updated_at) "
               "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
               "ON CONFLICT(title_key, kind) DO UPDATE SET mode=excluded.mode, path=excluded.path, crop=excluded.crop, "
               "pool=excluded.pool, updated_at=excluded.updated_at",
               (title_key, kind, mode, (c.get("path") or "") if keep else "", (c.get("crop") or "") if keep else "",
                c.get("logo") or "", json.dumps(pool), time.time()))
    return choice(title_key, kind)


def rotation_pick(c: dict, *, advance: bool = False, today: date | None = None,
                  rng: random.Random | None = None) -> tuple[dict | None, list[str]]:
    """Today's image of a rotation ({path, crop}) and the paths coming next.
    Like the posters' rotation: a shuffled deck that runs through every image
    before one repeats, moved on only by the nightly run (*advance*), on a new
    day; previews and pushes show today's."""
    pool = c.get("pool") or []
    if not pool:
        return None, []
    paths = [p["path"] for p in pool]
    deck, pos, current = list(c.get("deck") or []), int(c.get("deck_pos") or 0), c.get("current")
    day, rng, changed = (today or date.today()).isoformat(), rng or random.Random(), False
    if sorted(deck) != sorted(paths) or not (0 <= pos < len(deck)):
        deck, pos, changed = rules._shuffled(paths, current, rng), 0, True   # a new pool: a fresh deck
    elif advance and c.get("rotated_on") not in (None, day):
        pos += 1
        if pos >= len(deck):
            deck, pos = rules._shuffled(paths, deck[-1], rng), 0
        changed = True
    if advance or changed:
        db.execute("UPDATE jf_art SET deck = ?, deck_pos = ?, current = ?, rotated_on = ? "
                   "WHERE title_key = ? AND kind = ?",
                   (json.dumps(deck), pos, deck[pos], day if advance else c.get("rotated_on"),
                    c["title_key"], c["kind"]))
    return next(p for p in pool if p["path"] == deck[pos]), deck[pos + 1:]


def chosen(c: dict, *, advance: bool = False) -> dict | None:
    """The image you chose for this title today ({path, crop}): the pinned one,
    or today's of the rotation.  None when it is left to Studio or Jellyfin."""
    if c["mode"] == "pinned" and c.get("path"):
        return {"path": c["path"], "crop": c.get("crop") or ""}
    if c["mode"] == "rotation":
        return rotation_pick(c, advance=advance)[0]
    return None


# ── Picking ─────────────────────────────────────────────────────────────────

def _fits(c: dict, r: dict) -> bool:
    """Exactly the rule's size (min_w x min_h): bigger counts as off-size too."""
    return (c.get("width") or 0) == r["min_w"] and (c.get("height") or 0) == r["min_h"]


def auto_backdrop(cands: dict, r: dict | None = None) -> str | None:
    """The best background meeting the rules, textless first if asked; None
    when nothing qualifies (Jellyfin's stays)."""
    r = r or library_rules()["backdrop"]
    ok = [c for c in cands.get("backdrops") or [] if _fits(c, r)]
    if r["textless"]:
        ok.sort(key=lambda c: c.get("language") is not None)   # stable: providers' order within each
    return ok[0]["path"] if ok else None


def titled_backdrop(cands: dict, languages: list[str]) -> str | None:
    """A backdrop with the title on it, in the first language that has one."""
    tagged = [c for c in cands.get("backdrops") or [] if c.get("language")]
    for lang in languages + [None]:
        for c in tagged:
            if lang is None or (c["language"] or "").split("-")[0] == lang:
                return c["path"]
    return None


def fits_rules(c: dict) -> bool:
    return _fits(c, library_rules()["backdrop"])


# ── Getting the bytes ───────────────────────────────────────────────────────

async def fetch(path: str) -> bytes:
    """An image as stored: TMDB path (original size), provider url, custom
    upload, StageMedia url, or jf-chapter:<item>:<index> (a frame Jellyfin
    extracted from the file)."""
    if path.startswith("custom:"):
        import art_overrides
        data = art_overrides.custom_art_bytes(path)
        if data is None:
            raise RuntimeError("That uploaded image is missing")
        return data
    if path.startswith("jf-chapter:"):
        from . import engine
        _, item_id, index = path.split(":")
        data, _ = await engine.shared_client().image(item_id, f"Chapter/{int(index)}")
        return data
    from . import stage
    if stage.is_stage_host(path):
        return await stage.image_bytes(path)
    url = path if path.startswith("http") else f"https://image.tmdb.org/t/p/original{path}"
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=8.0), follow_redirects=True) as http:
        r = await http.get(url)
    if r.status_code != 200:
        raise RuntimeError(f"Image download failed (HTTP {r.status_code})")
    return r.content


def _frame(data: bytes, crop: str, aspect: float, max_w: int = 3840,
           exact: tuple[int, int] | None = None) -> tuple[bytes, str]:
    """The image framed to *aspect* (x,y,zoom as the poster frames), as JPEG;
    PNGs (logos) and unframed images pass through.  With *exact* (w, h) it
    comes out at exactly that size, cropped around its middle when no frame
    was chosen; an image already that size passes through."""
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    if not crop and (not exact or im.size == tuple(exact)):
        fmt = (im.format or "JPEG").upper()
        return data, "image/png" if fmt == "PNG" else "image/webp" if fmt == "WEBP" else "image/jpeg"
    x, y, zoom = (float(p) for p in crop.split(",")) if crop else (0.5, 0.5, 1.0)
    im = im.convert("RGB")
    base_w = min(im.width, im.height * aspect)
    cw = base_w / max(1.0, zoom)
    ch = cw / aspect
    left, top = (im.width - cw) * x, (im.height - ch) * y
    im = im.crop((round(left), round(top), round(left + cw), round(top + ch)))
    if exact:
        if im.size != tuple(exact):
            im = im.resize(tuple(exact), Image.Resampling.LANCZOS)
    elif im.width > max_w:
        im = im.resize((max_w, round(max_w / aspect)), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=92)
    return out.getvalue(), "image/jpeg"


def _own_backdrop(data: bytes, crop: str) -> tuple[bytes, str]:
    """A backdrop you chose, as it is sent: framed as you framed it, and with
    "resize" on in Settings brought to exactly the rule's size."""
    r = library_rules()["backdrop"]
    if r["resize"] and r["min_w"] and r["min_h"]:
        return _frame(data, crop, r["min_w"] / r["min_h"], exact=(r["min_w"], r["min_h"]))
    return _frame(data, crop, ASPECT["backdrop"])


async def resolve(row: dict, kind: str, *, cands_loader=None, draft: dict | None = None,
                  style_str: str | None = None, landscape: bool = False,
                  advance: bool = False) -> tuple[bytes, str] | None:
    """The image Studio would send for this item and kind, or None to leave
    Jellyfin's.  *draft* ({mode, path, crop}) previews an unsaved choice;
    *style_str* a library style other than the applied one (the Style page's
    draft) and *landscape* the landscape thumb whatever the Thumb source.
    *advance* (the nightly run) moves a rotation on to the next day's image."""
    from . import engine
    key = rules.title_key(row)
    c = {**choice(key, kind), **(draft or {})}
    if c["mode"] == "keep":
        return None
    pin = chosen(c, advance=advance)      # {path, crop}: what you pinned, or today's of your rotation
    pinned = pin is not None
    from . import identity
    tmdb_id, imdb_id, tvdb_id = identity.ids(row)
    # Anything /poster can draw: a TMDB id isn't needed, an IMDb or TVDB id will do.
    renderable = identity.kind(row) == identity.POSTER and row.get("jf_type") != "Season"
    import config as _cfg
    # A generated thumb draws the style on whatever art it has, a pinned image included.
    generated = kind == "thumb" and renderable and (landscape or library_rules()["thumb"]["source"] == "landscape")
    if (kind == "thumb" and engine.in_process(row) and row.get("jf_type") != "Season"
            and (landscape or library_rules()["thumb"]["source"] == "landscape")):
        from . import stage
        style_str = style_str if style_str is not None else prefs.get("style_applied")
        try:
            return await stage.render_thumb(row, style_str, rules.resolve(key).params,
                                            art=pin["path"] if pinned else "", crop=pin["crop"] if pinned else "",
                                            logo=c.get("logo") or "")
        except stage.NoArt:
            if identity.kind(row) == identity.STAGE:
                raise
            return None   # a title with no image of yours yet: Jellyfin's thumb stays
    if pinned and not generated:
        data = await fetch(pin["path"])
        if kind == "backdrop":
            return _own_backdrop(data, pin["crop"])
        return _frame(data, pin["crop"], ASPECT.get(kind, 0) or 1)
    if not renderable:
        return None   # nothing to pick from automatically
    style_str = style_str if style_str is not None else prefs.get("style_applied")
    style = dict(parse_qsl(style_str, keep_blank_values=True))
    media = "tv" if row["jf_type"] == "Series" else "movie"
    if kind == "logo":
        if not (tmdb_id or imdb_id):
            return None   # /logo knows titles by TMDB or IMDb id only
        lang = style.get("logo_language") or "en"
        params = {"type": media, "lang": lang}
        if tmdb_id:
            params["tmdb_id"] = tmdb_id
        if imdb_id:
            params["imdb_id"] = imdb_id
        if _cfg.ACCESS_KEY:
            params["access_key"] = _cfg.ACCESS_KEY
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as http:
            r = await http.get(f"{engine.LOOPBACK}/logo?{urlencode(params)}")
        if r.status_code != 200:
            return None
        return r.content, r.headers.get("content-type", "image/png").split(";")[0]
    if generated:
        res = rules.resolve(key)
        # The poster's logo (and Never logos) carry over; its art doesn't.
        extra = {k: v for k, v in res.params.items() if not k.startswith("art_") or k in ("art_logo", "art_logo_exclude")}
        extra["shape"] = "landscape"
        logo = c.get("logo") or ""
        if logo == "none":
            extra.pop("art_logo", None)
            if pinned:
                extra["art_original"] = "true"
            else:
                extra["landscape_art"] = "original"
        elif logo:
            extra["art_logo"] = logo
        if pinned:
            extra["art_poster"] = await realize_path(pin["path"], pin["crop"])
        elif not tmdb_id:
            # No TMDB backdrop to draw on: the backdrop you chose for this title, when there is one.
            bd = chosen(choice(key, "backdrop"))
            if bd:
                extra["art_poster"] = await realize_path(bd["path"], bd["crop"])
        # The landscape layout has its own rating switch: follow a style that hides ratings.
        if (style.get("rating_display_mode") == "0" and "landscape_hide_rating" not in style
                and "landscape_hide_rating" not in extra):
            extra["landscape_hide_rating"] = "true"
        url = engine.poster_url(row, style_str, resolution=500, with_quality=False,
                                access_key=_cfg.ACCESS_KEY or "", extra=await realize(extra))
        async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=5.0)) as http:
            return await engine.render(http, url)
    cands = await (cands_loader(media, tmdb_id) if cands_loader else _load_cands(media, tmdb_id, imdb_id, tvdb_id))
    if kind == "backdrop":
        path = auto_backdrop(cands)
    else:
        path = titled_backdrop(cands, [style.get("logo_language") or "en", "en"]) or auto_backdrop(cands)
    return _frame(await fetch(path), "", 16 / 9) if path else None


async def _load_cands(media: str, tmdb_id, imdb_id=None, tvdb_id=None) -> dict:
    from . import candidates
    return (await candidates.for_title(media, tmdb_id, imdb_id=imdb_id, tvdb_id=tvdb_id))["candidates"]


# ── Pushed state ────────────────────────────────────────────────────────────

def state(jf_id: str, kind: str) -> dict:
    return db.query_one("SELECT * FROM item_images WHERE jf_id = ? AND kind = ?", (jf_id, kind)) or {}


def record_seen(jf_id: str, kind: str, tag: str | None) -> None:
    db.execute("INSERT INTO item_images (jf_id, kind, seen_tag) VALUES (?, ?, ?) "
               "ON CONFLICT(jf_id, kind) DO UPDATE SET seen_tag = excluded.seen_tag", (jf_id, kind, tag))


def record_pushed(jf_id: str, kind: str, image_hash: str, tag: str | None) -> None:
    db.execute("INSERT INTO item_images (jf_id, kind, pushed_hash, pushed_tag, seen_tag, pushed_at) "
               "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(jf_id, kind) DO UPDATE SET pushed_hash = excluded.pushed_hash, "
               "pushed_tag = excluded.pushed_tag, seen_tag = excluded.seen_tag, pushed_at = excluded.pushed_at",
               (jf_id, kind, image_hash, tag, tag, time.time()))


def summary_json(title_key: str) -> str:
    return json.dumps({k: choice(title_key, k) for k in KINDS})
