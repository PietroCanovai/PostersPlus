"""Every image Studio's editor offers for a title: TMDB, Fanart, TVDB and IMDb
posters, backdrops and logos, plus the title's own uploads.

Each provider is asked by whichever id the title has: TMDB needs a TMDB id,
TVDB takes its own id or finds the title by IMDb/TMDB id, Fanart takes a TMDB
or IMDb id (films) or a TVDB id (shows), IMDb's art (Cinemeta's Metahub CDN)
needs only the IMDb id and no key.  So a title TMDB doesn't know still has
images to choose from.

Uses the same helpers as the Artwork tab (main._tmdb_art_item and the
provider candidate functions), so a path picked here is one the renderer
already knows how to fetch; IMDb's are copied in when used (artwork.realize).
Cached briefly per title so paging through the editor doesn't repeat calls.
"""
from __future__ import annotations

import asyncio
import logging
import time

from . import db

logger = logging.getLogger("studio")

_TTL = 1800
_cache: dict[tuple, tuple[float, dict]] = {}
_KINDS = ("posters", "backdrops", "logos")


def _main():
    import main  # loaded by uvicorn; imported lazily to avoid a cycle
    return main


def _empty() -> dict[str, list[dict]]:
    return {k: [] for k in _KINDS}


async def _tvdb(client, media_type: str, tmdb_id, imdb_id, tvdb_id) -> tuple[dict, int | None]:
    """TVDB's artwork, by its own id when we have it; also the id, for Fanart."""
    import tvdb
    out = _empty()
    if not tvdb.tvdb_enabled():
        return out, None
    resolved = await tvdb.resolve_tvdb_id(client, media_type=media_type, tvdb_id_hint=tvdb_id,
                                          imdb_id=imdb_id, tmdb_id=tmdb_id)
    if not resolved:
        return out, None
    artworks = await tvdb.fetch_tvdb_artworks(client, resolved, media_type)
    for kind, source in (("posters", "posters"), ("logos", "logos"), ("backdrops", "backgrounds")):
        for art in artworks.get(source, []):
            code = art.get("language") or None
            out[kind].append({"path": art["url"], "thumb": art.get("thumb") or art["url"],
                              "language": tvdb._LANG_3_TO_2.get(code, code) if code else None,
                              "score": art.get("score", 0)})
    return out, resolved


async def _fanart(client, media_type: str, tmdb_id, imdb_id, tvdb_id) -> dict:
    import fanart
    if tmdb_id:
        return await fanart.artwork_candidates(client, media_type=media_type, tmdb_id=tmdb_id, imdb_id=imdb_id)
    # No TMDB id: fanart.tv knows films by IMDb id and shows by TVDB id.  Its
    # candidate function reads the id it is given as the film's, so a film
    # goes through it; a show is asked for here.
    if media_type == "movie":
        return await fanart.artwork_candidates(client, media_type="movie", tmdb_id=imdb_id) if imdb_id else _empty()
    import config as _cfg
    out = _empty()
    if not _cfg.FANART_API_KEY or not tvdb_id:
        return out
    resp = await client.get(f"{fanart._API}/tv/{tvdb_id}", params={"api_key": _cfg.FANART_API_KEY})
    data = resp.json() if resp.status_code == 200 else {}
    for kind, keys in (("posters", ("tvposter",)), ("logos", ("hdtvlogo", "clearlogo")),
                       ("backdrops", ("showbackground", "tvthumb"))):
        items = [a for key in keys for a in (data.get(key) or []) if a.get("url")]
        items.sort(key=lambda a: -int(a.get("likes") or 0))
        for a in items:
            code = (a.get("lang") or "").lower()
            out[kind].append({"path": a["url"], "thumb": a["url"].replace("/fanart/", "/preview/", 1),
                              "language": None if code in ("", "00") else fanart._LANG_FIXUPS.get(code, code),
                              "score": int(a.get("likes") or 0)})
    return out


async def _imdb(client, imdb_id) -> dict:
    """IMDb's own art, as Cinemeta's Metahub CDN serves it: one poster (with
    the title on it), one background, one logo."""
    import cinemeta
    out = _empty()
    if not imdb_id:
        return out
    logo = f"{cinemeta.METAHUB_BASE}/logo/medium/{imdb_id}/img"
    (has_poster, has_background), has_logo = await asyncio.gather(
        cinemeta.probe_art(client, imdb_id), cinemeta._head_ok(client, logo))
    if has_poster:
        out["posters"].append({"path": cinemeta.poster_url(imdb_id, "large"), "thumb": cinemeta.poster_url(imdb_id),
                               "language": "en", "width": 780, "height": 1170})
    if has_background:
        out["backdrops"].append({"path": cinemeta.background_url(imdb_id), "thumb": cinemeta.background_url(imdb_id, "medium"),
                                 "language": None, "width": 1920, "height": 1080})
    if has_logo:
        out["logos"].append({"path": logo, "thumb": logo, "language": "en"})
    return out


async def for_title(media_type: str, tmdb_id: str | None = None, *, imdb_id: str | None = None,
                    tvdb_id: str | None = None, force: bool = False) -> dict:
    key = (media_type, tmdb_id or "", imdb_id or "", tvdb_id or "")
    hit = _cache.get(key)
    if hit and not force and time.time() - hit[0] < _TTL:
        return hit[1]
    m = _main()
    import config as _cfg
    client, tmdb_key = m._HTTP_CLIENT, _cfg.SERVER_TMDB_KEY
    if client is None:
        raise RuntimeError("PostersPlus is still starting")

    out = _empty()
    result = {"title": None, "year": None, "overview": None, "candidates": out,
              "auto": {"poster": None, "logo": None}, "textless_pool": []}
    images = {}
    if tmdb_id and tmdb_key:
        meta = await m._coalesced_fetch_poster_metadata(client, tmdb_id, tmdb_key, media_type, "en")
        _gids, is_textless, logos, year, title, poster_path, backdrop_path, tmdb_data = meta
        # TMDB's own links to the other databases beat the ones Jellyfin had.
        imdb_id = tmdb_data.get("imdb_id") or imdb_id
        tvdb_id = tmdb_data.get("tvdb_id") or tvdb_id
        auto_poster = ({"path": poster_path, "kind": "poster"} if poster_path and is_textless
                       else {"path": backdrop_path, "kind": "backdrop"} if backdrop_path
                       else {"path": poster_path, "kind": "poster"} if poster_path else None)
        result.update({
            "title": title, "year": year, "overview": tmdb_data.get("overview"),
            "auto": {"poster": auto_poster,
                     "logo": m._default_logo_path(logos, "en", tmdb_data.get("original_language"))},
            "textless_pool": (tmdb_data.get("poster_pools") or {}).get("textless") or [],
        })
        resp = await m._proxy_tmdb_get(f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/images",
                                       {"api_key": tmdb_key})
        images = resp.json() if resp.status_code == 200 else {}
    for kind in out:
        for img in images.get(kind) or []:
            if img.get("file_path"):
                out[kind].append({**m._tmdb_art_item(img, kind), "provider": "tmdb"})

    try:
        tvdb_c, tvdb_resolved = await _tvdb(client, media_type, tmdb_id, imdb_id, tvdb_id)
    except Exception as exc:
        tvdb_c, tvdb_resolved = exc, None
    fanart_c, imdb_c = await asyncio.gather(
        _fanart(client, media_type, tmdb_id, imdb_id, tvdb_resolved or tvdb_id),
        _imdb(client, imdb_id), return_exceptions=True)
    for provider, found in (("fanart", fanart_c), ("tvdb", tvdb_c), ("imdb", imdb_c)):
        if isinstance(found, Exception):
            logger.warning(f"Studio: {provider} candidates failed for {media_type} {key[1:]}: {found}")
            continue
        for kind in out:
            for c in found.get(kind) or []:
                out[kind].append({**c, "provider": provider})
    # Textless first (what our logo goes on), each group best-first as the providers rank them.
    out["posters"].sort(key=lambda c: c["language"] is not None)
    out["logos"].sort(key=lambda c: c["language"] not in (None, "en"))
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
