"""Which title a Jellyfin item is, and so how its poster gets made.

By default Studio follows Jellyfin's provider ids.  On a title's page you can
say otherwise (items.match_source):

  auto    Jellyfin's ids: StageMedia first (theatre), then TMDB, IMDb, TVDB
  tmdb    the TMDB id you picked
  imdb    an IMDb id (TMDB's id for it is looked up when TMDB knows it)
  tvdb    a TVDB id (same)
  stage   a StageMedia show (the id Encora uses for the show)
  none    no database knows it: drawn from your own images

That gives one of three ways to make the poster (kind):

  poster  PostersPlus's /poster, by TMDB id, IMDb id or TVDB id; the last two
          need no TMDB at all
  stage   studio.stage, from StageMedia's art or your images
  own     studio.stage's compositor on an image of yours (nothing to look up)
"""
from __future__ import annotations

import re
import time

SOURCES = ("auto", "tmdb", "imdb", "tvdb", "stage", "none")
POSTER, STAGE, OWN = "poster", "stage", "own"

_IMDB_RE = re.compile(r"tt\d{1,10}")
_cache: dict[tuple, tuple[float, dict | None]] = {}
_TTL = 6 * 3600


def source(row: dict) -> str:
    s = row.get("match_source") or ""
    if s in SOURCES and s != "auto":
        return s
    return "tmdb" if row.get("manual_tmdb_id") else "auto"   # matches made before match_source existed


def stage_id(row: dict) -> str | None:
    """The StageMedia show this title belongs to (seasons never do: a theatre
    show's seasons are its productions)."""
    src = source(row)
    if src not in ("auto", "stage") or row.get("jf_type") == "Season":
        return None
    return (row.get("manual_stage_id") if src == "stage" else None) or row.get("stage_show_id") or None


def _ids(row: dict) -> tuple[str | None, str | None, str | None]:
    """(tmdb, imdb, tvdb) as chosen, leaving theatre aside."""
    src = source(row)
    if src == "auto":
        tvdb = str(row.get("tvdb_id") or "")
        return row.get("tmdb_id") or None, row.get("imdb_id") or None, tvdb if tvdb.isdigit() else None
    if src == "tmdb":
        # Jellyfin's IMDb id rides along; the renderer keeps it only when TMDB links the two.
        return row.get("manual_tmdb_id") or None, row.get("imdb_id") or None, None
    if src == "imdb":
        return row.get("manual_tmdb_id") or None, row.get("manual_imdb_id") or None, None
    if src == "tvdb":
        return row.get("manual_tmdb_id") or None, None, row.get("manual_tvdb_id") or None
    return None, None, None


def key_tmdb_id(row: dict) -> str | None:
    """The TMDB id rules are filed under (rules.title_key)."""
    return _ids(row)[0]


def kind(row: dict) -> str:
    from . import stage
    # Jellyfin's StageMedia id only counts once there is a key to use it with;
    # one you chose yourself always does (the editor then asks for the key).
    if stage_id(row) and (source(row) == "stage" or stage.enabled()):
        return STAGE
    return POSTER if any(_ids(row)) else OWN


def ids(row: dict) -> tuple[str | None, str | None, str | None]:
    """(tmdb, imdb, tvdb) for the renderer; all None for theatre and own-image titles."""
    return _ids(row) if kind(row) == POSTER else (None, None, None)


def tmdb_id(row: dict) -> str | None:
    return ids(row)[0]


def render_ids(row: dict) -> dict:
    """The /poster parameters that name the title."""
    tmdb, imdb, tvdb = ids(row)
    out = {}
    if tmdb:
        out["tmdb_id"] = tmdb
    if imdb:
        out["imdb_id"] = imdb
    if not out and tvdb:
        out["stremio_id"] = f"tvdb:{tvdb}"   # the renderer's TVDB-only path
    return out


def describe(row: dict) -> dict:
    tmdb, imdb, tvdb = ids(row)
    return {
        "source": source(row), "kind": kind(row),
        "tmdb_id": tmdb, "imdb_id": imdb, "tvdb_id": tvdb, "stage_id": stage_id(row) if kind(row) == STAGE else None,
        "jellyfin": {"tmdb_id": row.get("tmdb_id"), "imdb_id": row.get("imdb_id"), "tvdb_id": row.get("tvdb_id"),
                     "stage_id": row.get("stage_show_id")},
    }


# ── Reading what you typed ──────────────────────────────────────────────────

def parse_id(src: str, raw: str) -> str:
    """The id out of what was typed or pasted (an id or the page's link)."""
    raw = (raw or "").strip()
    if src == "imdb":
        m = _IMDB_RE.search(raw)
        if not m:
            raise ValueError("An IMDb id looks like tt0113277")
        return m.group(0)
    if src == "tmdb":
        m = re.search(r"themoviedb\.org/(?:movie|tv)/(\d+)", raw) or re.fullmatch(r"(\d{1,10})", raw)
        if not m:
            raise ValueError("A TMDB id is a number, like 949")
        return m.group(1)
    if src == "tvdb":
        m = re.search(r"[?&]id=(\d+)", raw) or re.fullmatch(r"(\d{1,10})", raw)
        if not m:
            raise ValueError("A TVDB id is a number, like 81189 (the series page shows it)")
        return m.group(1)
    if src == "stage":
        m = re.search(r"/shows?/(\d+)", raw) or re.fullmatch(r"(\d{1,10})", raw)
        if not m:
            raise ValueError("A show id is a number")
        return m.group(1)
    raise ValueError("Unknown kind of id")


# ── TMDB's id for an IMDb or TVDB id ────────────────────────────────────────

async def find_tmdb(src: str, ext_id: str, media_type: str) -> dict | None:
    """{tmdb_id, title, year} when TMDB lists this IMDb/TVDB id as a film or a
    show (*media_type*), else None: also when PostersPlus has no TMDB key, in
    which case the renderer works from the IMDb or TVDB id alone."""
    import config as _cfg
    import main
    if not _cfg.SERVER_TMDB_KEY:
        return None
    key = (src, ext_id, media_type)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    resp = await main._proxy_tmdb_get(f"https://api.themoviedb.org/3/find/{ext_id}",
                                      {"api_key": _cfg.SERVER_TMDB_KEY, "external_source": f"{src}_id"})
    if resp.status_code != 200:
        raise RuntimeError(f"TMDB lookup returned {resp.status_code}")
    found = (resp.json().get("tv_results" if media_type == "tv" else "movie_results") or [])
    out = None
    if found:
        r = found[0]
        date = r.get("release_date") or r.get("first_air_date") or ""
        out = {"tmdb_id": str(r["id"]), "title": r.get("title") or r.get("name") or "", "year": date[:4]}
    _cache[key] = (time.time(), out)
    return out
