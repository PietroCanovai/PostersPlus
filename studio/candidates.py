"""Every image Studio's editor offers for a title: TMDB, Fanart and TVDB
posters, backdrops and logos, plus the title's own uploads.

Uses the same helpers as the Artwork tab (main._tmdb_art_item and the
provider candidate functions), so a path picked here is one the renderer
already knows how to fetch.  Cached briefly per title so paging through the
editor doesn't repeat provider calls.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import db

logger = logging.getLogger("studio")

_TTL = 1800
_cache: dict[tuple[str, str], tuple[float, dict]] = {}


def _main():
    import main  # loaded by uvicorn; imported lazily to avoid a cycle
    return main


async def for_title(media_type: str, tmdb_id: str, *, force: bool = False) -> dict:
    key = (media_type, tmdb_id)
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < _TTL:
        return hit[1]
    m = _main()
    import fanart
    import tvdb
    import config as _cfg
    if m._HTTP_CLIENT is None or not _cfg.SERVER_TMDB_KEY:
        raise RuntimeError("PostersPlus has no TMDB key; add it in the admin dashboard")
    client, tmdb_key = m._HTTP_CLIENT, _cfg.SERVER_TMDB_KEY

    meta = await m._coalesced_fetch_poster_metadata(client, tmdb_id, tmdb_key, media_type, "en")
    _gids, is_textless, logos, year, title, poster_path, backdrop_path, tmdb_data = meta
    imdb_id = tmdb_data.get("imdb_id")

    images_resp, fanart_c, tvdb_c = await asyncio.gather(
        m._proxy_tmdb_get(f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images", {"api_key": tmdb_key}),
        fanart.artwork_candidates(client, media_type=media_type, tmdb_id=tmdb_id, imdb_id=imdb_id),
        tvdb.artwork_candidates(client, media_type=media_type, tmdb_id=tmdb_id, imdb_id=imdb_id),
        return_exceptions=True,
    )
    images = images_resp.json() if not isinstance(images_resp, Exception) and images_resp.status_code == 200 else {}

    out = {"posters": [], "backdrops": [], "logos": []}
    for kind in out:
        for img in images.get(kind) or []:
            if img.get("file_path"):
                out[kind].append({**m._tmdb_art_item(img, kind), "provider": "tmdb"})
    for provider, found in (("fanart", fanart_c), ("tvdb", tvdb_c)):
        if isinstance(found, Exception):
            logger.warning(f"Studio: {provider} candidates failed for {media_type} {tmdb_id}: {found}")
            continue
        for kind in out:
            for c in found.get(kind) or []:
                out[kind].append({**c, "provider": provider})
    # Textless first (what our logo goes on), each group best-first as the providers rank them.
    out["posters"].sort(key=lambda c: c["language"] is not None)
    out["logos"].sort(key=lambda c: c["language"] not in (None, "en"))

    auto_poster = ({"path": poster_path, "kind": "poster"} if poster_path and is_textless
                   else {"path": backdrop_path, "kind": "backdrop"} if backdrop_path
                   else {"path": poster_path, "kind": "poster"} if poster_path else None)
    result = {
        "title": title, "year": year, "overview": tmdb_data.get("overview"),
        "candidates": out,
        "auto": {"poster": auto_poster, "logo": m._default_logo_path(logos, "en", tmdb_data.get("original_language"))},
        "textless_pool": (tmdb_data.get("poster_pools") or {}).get("textless") or [],
    }
    _cache[key] = (time.time(), result)
    return result


def uploads(title_key: str) -> list[str]:
    """Custom images this title's looks use (so the editor can show them again)."""
    paths = set()
    for r in db.query("SELECT poster, logo FROM looks WHERE title_key = ?", (title_key,)):
        for p in (r["poster"], r["logo"]):
            if p and p.startswith("custom:"):
                paths.add(p)
    return sorted(paths)
