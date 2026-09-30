"""Theatre recordings from StageMedia (stagemedia.me), as imported into Jellyfin
by the Encora plugin.

These titles have no TMDB or IMDb id, only the plugin's StageMediaShowId, so
PostersPlus's /poster pipeline (TMDB-spined) can't draw them.  Studio renders
them itself with the same compositor: the chosen StageMedia poster (or your
upload) framed to 2:3, then build_poster with the global style and the
title's look — fades, your logo or the show's name as text, colours.

API: GET https://stagemedia.me/api/images?show_id=<id>, Authorization: Bearer
<key> → {"posters": [url, ...]}.  No title search exists.
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


def key() -> str:
    return prefs.get("stagemedia_key") or ""


def enabled() -> bool:
    return bool(key())


def show_name(item_name: str) -> str:
    """"Hadestown - Broadway, 09-02-2024 - bikinibottomday" → "Hadestown"."""
    return re.split(r"\s+-\s+", item_name or "", maxsplit=1)[0].strip() or item_name


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
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            r = await http.get(API, params={"show_id": show_id},
                               headers={"Authorization": f"Bearer {key()}", "Accept": "application/json",
                                        "User-Agent": "PostersPlus-Studio/1.0"})
    except httpx.HTTPError as exc:
        raise StageError(f"Can't reach StageMedia ({type(exc).__name__})")
    if r.status_code in (401, 403):
        raise StageError("StageMedia rejected the API key")
    if r.status_code != 200:
        raise StageError(f"StageMedia answered HTTP {r.status_code}")
    try:
        found = [p for p in (r.json().get("posters") or []) if isinstance(p, str) and valid_poster(p)][:60]
    except ValueError:
        raise StageError("StageMedia sent something that isn't JSON")
    _lists[show_id] = (time.time(), found)
    return found


async def image_bytes(url: str) -> bytes:
    """A StageMedia (or other public) image, cached on disk."""
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


async def render(row: dict, style: str, params: dict, resolution: int) -> tuple[bytes, str]:
    """The finished poster for a theatre title: (bytes, content type)."""
    from urllib.parse import parse_qsl

    import art_overrides
    import main
    from PIL import Image

    merged = {**dict(parse_qsl(style, keep_blank_values=True)), **params}
    poster = merged.pop("art_poster", "")
    crop_token = merged.pop("art_crop", "")
    own_title = merged.pop("art_original", "") in ("1", "true")
    if not poster:
        found = await posters(row["stage_show_id"])
        if not found:
            raise StageError("StageMedia has no poster for this show yet; upload one in the editor")
        poster = found[0]
    data = await image_bytes(poster)
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

    def _compose() -> bytes:
        art = Image.open(io.BytesIO(data)).convert("RGB")
        art = art.crop(crop.box(art.width, art.height)).resize(size, Image.Resampling.LANCZOS)
        image = art.convert("RGBA")
        out = main.build_poster(
            image, "—", "Theatre", cfg, logo=logo,
            fallback_title=None if (own_title or logo is not None) else show_name(row["name"]),
        )
        return main._encode_poster(out)

    async with main._get_render_semaphore():
        body = await asyncio.get_running_loop().run_in_executor(None, _compose)
    import config as _cfg
    return body, f"image/{_cfg.IMAGE_FORMAT}"
