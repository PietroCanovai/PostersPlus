"""Theatre recordings from StageMedia (stagemedia.me), as imported into Jellyfin
by the Encora plugin.

These titles have no TMDB or IMDb id, only the plugin's StageMediaShowId, so
PostersPlus's /poster pipeline (TMDB-spined) can't draw them.  Studio renders
them itself with the same compositor: the chosen StageMedia poster (or your
upload) framed to 2:3, then build_poster with the global style and the
title's look — fades, your logo or the show's name as text, colours.

API: GET https://stagemedia.me/api/images?show_id=<id>&actor_ids=<ids>,
Authorization: Bearer <key> → {"posters": [url, ...], "performers": [...]}.
actor_ids is required (400 "No actors" without it); the Encora plugin sends
its recording's performer ids, or 1 when it has none.  No title search exists.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import os
import re
import time
from urllib.parse import urlsplit

import httpx

from . import db, prefs

logger = logging.getLogger("studio")

API = "https://stagemedia.me/api/images"
LIST_TTL = 24 * 3600
_lists: dict[str, tuple[float, list[str]]] = {}
MAX_BYTES = 25 * 1024 * 1024


class StageError(Exception):
    pass


class NoArt(StageError):
    """StageMedia has nothing for this show and you haven't uploaded anything:
    the run skips it and leaves Jellyfin's poster alone."""


def key() -> str:
    return prefs.get("stagemedia_key") or ""


def enabled() -> bool:
    return bool(key())


def show_name(item_name: str) -> str:
    """"Hadestown - Broadway, 09-02-2024 - bikinibottomday" → "Hadestown"."""
    return re.split(r"\s+-\s+", item_name or "", maxsplit=1)[0].strip() or item_name


def title_text(row: dict) -> str:
    """The name written on the poster: the show's for theatre, else the item's."""
    from . import identity
    return show_name(row["name"]) if identity.stage_id(row) else row["name"]


async def _first_poster(row: dict, what: str) -> str:
    """StageMedia's first image for the title's show; NoArt when there is none,
    or when the title isn't a StageMedia show at all (drawn from your images)."""
    from . import identity
    show_id = identity.stage_id(row)
    if not show_id:
        raise NoArt("No image yet: pin one of your own in the editor")
    found = await posters(show_id)
    if not found:
        raise NoArt(f"StageMedia has no {what} for this show yet; upload one in the editor")
    return found[0]


def is_stage_host(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    return host == "stagemedia.me" or host.endswith(".stagemedia.me")


def valid_poster(url: str) -> bool:
    parts = urlsplit(url or "")
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and len(url) < 2048


def _cache_dir() -> str:
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "studio_stage")
    os.makedirs(path, exist_ok=True)
    return path


async def posters(show_id: str, *, force: bool = False) -> list[str]:
    if not enabled():
        raise StageError("Add your StageMedia API key in Settings to see theatre posters")
    hit = _lists.get(show_id)
    if hit and not force and time.time() - hit[0] < LIST_TTL:
        return hit[1]
    r = None
    for attempt in (1, 2):   # StageMedia is sometimes slow: one retry before giving up
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
                # The API refuses a request without actor_ids ("No actors").  Posters
                # belong to the show; the ids only pick performer photos, so this
                # sends the same placeholder the Encora plugin falls back to.
                r = await http.get(API, params={"show_id": show_id, "actor_ids": "1"},
                                   headers={"Authorization": f"Bearer {key()}", "Accept": "application/json",
                                            "User-Agent": "PostersPlus-Studio/1.0"})
            break
        except httpx.HTTPError as exc:
            if attempt == 2:
                raise StageError(f"Can't reach StageMedia ({type(exc).__name__})")
    if r.status_code in (401, 403):
        raise StageError("StageMedia rejected the API key")
    try:
        data = r.json()
    except ValueError:
        data = None
    # A show StageMedia has no artwork for answers 400 {"posters": [], "error": "No actors"}:
    # that is "nothing yet", not a failure.
    if not isinstance(data, dict) or "posters" not in data:
        raise StageError(f"StageMedia answered HTTP {r.status_code}")
    found = [p for p in (data.get("posters") or []) if isinstance(p, str) and valid_poster(p)][:60]
    _lists[show_id] = (time.time(), found)
    return found


async def image_bytes(url: str) -> bytes:
    """A StageMedia (or other public) image, cached on disk; also a frame."""
    from . import artwork
    if artwork.is_frame(url):
        return await artwork.fetch(url)
    if url.startswith("custom:"):
        import art_overrides
        data = await asyncio.to_thread(art_overrides.custom_art_bytes, url)
        if data is None:
            raise StageError("That uploaded image is missing")
        return data
    if not valid_poster(url):
        raise StageError("Not an image link Studio can use")
    path = os.path.join(_cache_dir(), hashlib.sha256(url.encode()).hexdigest()[:24])
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read()
    if is_stage_host(url):
        # The key only ever travels to StageMedia itself.
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=8.0), follow_redirects=False) as http:
            r = await http.get(url, headers={"Authorization": f"Bearer {key()}", "User-Agent": "PostersPlus-Studio/1.0"})
        if r.status_code != 200:
            raise StageError(f"Poster download failed (HTTP {r.status_code})")
        data = r.content
    else:
        import art_overrides
        import main
        try:
            data = await art_overrides.download_custom_url(main._HTTP_CLIENT, url)
        except ValueError as exc:
            raise StageError(f"Poster download failed: {exc}")
    if len(data) > MAX_BYTES:
        raise StageError("That poster is over 25 MB")
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)
    return data


async def thumbnail(url: str, width: int = 342) -> bytes:
    from PIL import Image
    data = await image_bytes(url)

    def _small() -> bytes:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        im.thumbnail((width, width * 2))
        out = io.BytesIO()
        im.save(out, format="JPEG", quality=85)
        return out.getvalue()
    return await asyncio.to_thread(_small)


def _stage_canvas(art, size: tuple[int, int], framed: bool):
    """A 16:9 canvas from theatre art.  Framed (or wide) art fills it; portrait
    key art is shown whole on the right, over a blurred, darkened copy of
    itself, leaving the left for the title like every other thumb."""
    from PIL import Image, ImageEnhance, ImageFilter
    w, h = size
    art = art.convert("RGB")
    if framed or art.width / art.height >= 1.3:
        scale = max(w / art.width, h / art.height)
        im = art.resize((round(art.width * scale), round(art.height * scale)), Image.Resampling.LANCZOS)
        left, top = (im.width - w) // 2, (im.height - h) // 2
        return im.crop((left, top, left + w, top + h))
    scale = max(w / art.width, h / art.height)
    bg = art.resize((round(art.width * scale), round(art.height * scale)), Image.Resampling.LANCZOS)
    top = (bg.height - h) // 3
    bg = bg.crop(((bg.width - w) // 2, top, (bg.width - w) // 2 + w, top + h)).filter(ImageFilter.GaussianBlur(w / 40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    fg_h = h
    fg = art.resize((round(art.width * fg_h / art.height), fg_h), Image.Resampling.LANCZOS)
    bg.paste(fg, (w - fg.width - round(w * 0.06), 0))
    return bg


async def render_thumb(row: dict, style: str, params: dict, *, art: str = "", crop: str = "",
                       logo: str = "") -> tuple[bytes, str]:
    """A theatre title's generated thumb: PostersPlus's landscape layout (the
    thumb style, logo or the show's name) on the show's art.  *art* is a
    pinned image (framed with *crop*), else the poster's art, else
    StageMedia's first poster.  *logo* as the Thumb tab's choice."""
    from urllib.parse import parse_qsl

    import config as _cfg
    import landscape
    import main
    from PIL import Image

    from . import artwork
    merged = {**dict(parse_qsl(style, keep_blank_values=True)), **params}
    poster_art = merged.pop("art_poster", "")
    merged.pop("art_crop", None)
    own_title = merged.pop("art_original", "") in ("1", "true")
    for k in ("studio_template", "playbill_venue"):
        merged.pop(k, None)
    merged.setdefault("landscape_hide_rating", "true")   # no scores for theatre
    merged["shape"] = "landscape"
    source = art or poster_art
    if not source:
        source = await _first_poster(row, "art")
    data = await (artwork.fetch(source) if artwork.is_frame(source) else image_bytes(source))
    if art and crop:
        data, _ = artwork._frame(data, crop, 16 / 9)
    cfg = main.build_request_config(merged)
    logo_ref = logo if logo not in ("", "text", "none") else ("" if logo else cfg.art_logo)
    logo_img = None
    if logo_ref and logo_ref != "text":
        try:
            logo_img = await main.fetch_logo_image(main._HTTP_CLIENT, logo_ref)
        except Exception as exc:
            logger.warning(f"Studio: theatre thumb logo {logo_ref} failed ({exc}); using the name")
    # The name as text unless the art already carries it (None) or a logo is drawn.
    wants_text = logo_img is None and logo != "none" and not (own_title and not art)
    size = (_cfg.LANDSCAPE_WIDTH, _cfg.LANDSCAPE_HEIGHT)

    def _compose() -> bytes:
        canvas = _stage_canvas(Image.open(io.BytesIO(data)), size, framed=bool(art and crop)).convert("RGBA")
        out = landscape.build_landscape(canvas, "—", "Theatre", cfg, logo=logo_img,
                                        fallback_title=title_text(row) if wants_text else None)
        return main._encode_poster(out)

    async with main._get_render_semaphore():
        body = await asyncio.get_running_loop().run_in_executor(None, _compose)
    return body, f"image/{_cfg.IMAGE_FORMAT}"


async def render(row: dict, style: str, params: dict, resolution: int) -> tuple[bytes, str]:
    """The finished poster for a theatre title: (bytes, content type)."""
    from urllib.parse import parse_qsl

    import art_overrides
    import main
    from PIL import Image

    merged = {**dict(parse_qsl(style, keep_blank_values=True)), **params}
    template = merged.pop("studio_template", "")
    venue = merged.pop("playbill_venue", None)
    poster = merged.pop("art_poster", "")
    crop_token = merged.pop("art_crop", "")
    own_title = merged.pop("art_original", "") in ("1", "true")
    if not poster:
        poster = await _first_poster(row, "poster")
    data = await image_bytes(poster)
    from . import artwork
    is_frame = artwork.is_frame(poster)
    cfg = main.build_request_config(merged)
    width = resolution if resolution in (500, 780, 1000, 1500, 2000) else 1000
    size = (width, width * 3 // 2)
    crop = art_overrides.parse_crop(crop_token) if crop_token else art_overrides.Crop()

    logo = None
    logo_ref = cfg.art_logo
    if logo_ref and logo_ref != "text" and not own_title:
        try:
            logo = await main.fetch_logo_image(main._HTTP_CLIENT, logo_ref)
        except Exception as exc:
            logger.warning(f"Studio: theatre logo {logo_ref} failed ({exc}); using the title as text")

    # The venue: yours, else the show's theatre (its Broadway house, else its West End
    # one: playbill.pick_venue, read at the scan), else the recording's name
    # ("Show - Broadway, date - …"), else the show's first production in Jellyfin.
    from . import playbill as _playbill
    playbill_venue = venue if venue is not None else (
        row.get("venue") or _playbill.venue_from_name(row["name"])
        or (row.get("productions") or "").split("|")[0])

    def _compose() -> bytes:
        if template == "playbill":
            from . import playbill
            art = Image.open(io.BytesIO(data))
            out = playbill.compose(art, size=size, logo=logo, venue=playbill_venue,
                                   crop=(crop.x, crop.y, crop.zoom))
            return main._encode_poster(out)
        art = Image.open(io.BytesIO(data)).convert("RGB")
        art = art.crop(crop.box(art.width, art.height)).resize(size, Image.Resampling.LANCZOS)
        image = art.convert("RGBA")
        # StageMedia's posters are the show's key art, title included: the
        # name is only written on when you ask for it (Title as text), or on
        # an image of yours that has no title of its own.
        wants_text = logo is None and not own_title and (
            cfg.art_logo == "text" or ((poster.startswith("custom:") or is_frame) and not cfg.art_logo))
        out = main.build_poster(
            image, "—", "Theatre", cfg, logo=logo,
            fallback_title=title_text(row) if wants_text else None,
        )
        return main._encode_poster(out)

    async with main._get_render_semaphore():
        body = await asyncio.get_running_loop().run_in_executor(None, _compose)
    import config as _cfg
    return body, f"image/{_cfg.IMAGE_FORMAT}"
