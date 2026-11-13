"""Season posters.

A season's poster is the image alone: no notch, title, logo, shading or
frame.  A season poster usually carries the show's name and the season
number already, so nothing is drawn over it.  The image is TMDB's best
season poster (a titled one first, English before others, then a textless
one), unless the season's own look names one; framed 2:3 with the look's
crop and drawn by Studio itself (no /poster call, so the global style and
the show's style don't apply).
"""
from __future__ import annotations

import asyncio
import io
import logging
import time

logger = logging.getLogger("studio")

_TTL = 24 * 3600
_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}

WIDTHS = (500, 780, 1000, 1500, 2000)


class NoArt(Exception):
    """Nothing to draw the season from: the run leaves Jellyfin's poster alone."""


def label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


def _rank(c: dict) -> tuple:
    """Titled before textless (the title is the point), English first."""
    lang = c.get("language")
    return (lang is None, lang != "en", -(c.get("score") or 0))


async def season_posters(tmdb_id: str, number: int) -> list[dict]:
    """TMDB's posters for one season, best first: [{path, language, score, ...}]."""
    key = (str(tmdb_id), int(number))
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    import config as _cfg
    import main
    out: list[dict] = []
    if _cfg.SERVER_TMDB_KEY:
        try:
            r = await main._proxy_tmdb_get(
                f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{number}/images", {"api_key": _cfg.SERVER_TMDB_KEY})
            if r.status_code == 200:
                out = [{**main._tmdb_art_item(p, "posters"), "provider": "tmdb", "season": number}
                       for p in (r.json().get("posters") or []) if p.get("file_path")]
        except Exception as exc:
            logger.warning(f"Studio: season {number} posters for {tmdb_id} failed: {exc}")
    out.sort(key=_rank)
    _cache[key] = (time.time(), out)
    return out


async def params_for(row: dict, own: dict) -> dict:
    """The season's image and crop: its own look's, else TMDB's best (skipping Never)."""
    from . import identity
    params = {k: v for k, v in own.items() if k in ("art_poster", "art_crop")}
    if "art_poster" not in params:
        banned = set((own.get("art_exclude") or "").split(","))
        best = next((p for p in await season_posters(identity.tmdb_id(row), int(row.get("season_number") or 0))
                     if p["path"] not in banned), None)
        if best is not None:
            params["art_poster"] = best["path"]
    return params


def compose(data: bytes, crop_token: str, width: int):
    """The image framed 2:3 (the crop, else centred) at *width*: a PIL image."""
    import art_overrides
    from PIL import Image
    crop = (art_overrides.parse_crop(crop_token) if crop_token else None) or art_overrides.Crop()
    art = Image.open(io.BytesIO(data)).convert("RGB")
    size = (width, width * 3 // 2)
    return art.crop(crop.box(art.width, art.height)).resize(size, Image.Resampling.LANCZOS)


async def render(params: dict, resolution: int) -> tuple[bytes, str]:
    """The season's poster: (bytes, content type).  NoArt when there's no image."""
    import config as _cfg
    import main

    from . import artwork
    path = params.get("art_poster") or ""
    if not path:
        raise NoArt("TMDB has no poster for this season: pick one in the editor")
    data = await artwork.fetch(path)
    width = resolution if resolution in WIDTHS else 1000

    def _make() -> bytes:
        return main._encode_poster(compose(data, params.get("art_crop") or "", width))

    async with main._get_render_semaphore():
        body = await asyncio.get_running_loop().run_in_executor(None, _make)
    return body, f"image/{_cfg.IMAGE_FORMAT}"
