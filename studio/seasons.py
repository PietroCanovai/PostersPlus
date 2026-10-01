"""Season posters.

A season is drawn through /poster like its show (same TMDB id, type tv), with:
  - its own art: TMDB's best season poster (textless first, with our logo;
    else a titled one served as it is), unless the season's look names one;
  - "Season N" (or "Specials") as the notch label;
  - the show's title-level style underneath its own rules, so a show's
    colours carry over to its seasons unless a season says otherwise.
"""
from __future__ import annotations

import logging
import time

from . import rules

logger = logging.getLogger("studio")

_TTL = 24 * 3600
_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}


def label(number: int) -> str:
    return "Specials" if number == 0 else f"Season {number}"


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
    out.sort(key=lambda c: (c["language"] is not None, c["language"] not in (None, "en"), -(c["score"] or 0)))
    _cache[key] = (time.time(), out)
    return out


async def params_for(row: dict, own: dict) -> dict:
    """Render parameters for a season: the show's style < season defaults < the season's own rules."""
    from . import identity
    tmdb_id = identity.tmdb_id(row)
    show_key = f"tmdb:tv:{tmdb_id}"
    params = dict(rules.get_title(show_key)["style"])
    number = int(row.get("season_number") or 0)
    params["notch_label"] = label(number)
    if "art_poster" not in own:
        banned = set((own.get("art_exclude") or "").split(","))
        best = next((p for p in await season_posters(tmdb_id, number)
                     if p["path"] not in banned), None)
        if best is not None:
            params["art_poster"] = best["path"]
            if best["language"]:
                params["art_original"] = "1"
    params.update(own)
    return params
