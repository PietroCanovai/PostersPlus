"""Encora (encora.it), for identifying theatre titles by hand.

A StageMedia show id is Encora's show id (the Encora plugin copies a
recording's metadata.show_id into StageMediaShowId), so Encora's API is how a
show is found: by name, or from one of its recordings.

  GET /api/shows/search?q=   → [{id, name, poster_url, year}]
  GET /api/recording/<id>    → {show, tour, metadata: {show_id, ...}, ...}

Bearer key (asked from Encora's support), 30 requests a minute.  Only ever
called when you search or link something in the editor, never by a run.
"""
from __future__ import annotations

import time

import httpx

from . import prefs

API = "https://encora.it/api"
_TTL = 3600
_cache: dict[str, tuple[float, object]] = {}


class EncoraError(Exception):
    pass


def key() -> str:
    return prefs.get("encora_key") or ""


def enabled() -> bool:
    return bool(key())


async def _get(path: str, params: dict | None = None, *, transport=None):
    if not enabled():
        raise EncoraError("Add your Encora API key in Settings to search Encora")
    ck = f"{path}?{sorted((params or {}).items())}"
    hit = _cache.get(ck)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0), transport=transport) as http:
            r = await http.get(f"{API}{path}", params=params,
                               headers={"Authorization": f"Bearer {key()}", "Accept": "application/json",
                                        "User-Agent": "PostersPlus-Studio/1.0"})
    except httpx.HTTPError as exc:
        raise EncoraError(f"Can't reach Encora ({type(exc).__name__})")
    if r.status_code in (401, 403):
        raise EncoraError("Encora rejected the API key")
    if r.status_code == 404:
        raise EncoraError("Encora doesn't know that id")
    if r.status_code == 429:
        raise EncoraError("Encora's limit of 30 requests a minute was reached: wait a minute")
    try:
        data = r.json() if r.status_code == 200 else None
    except ValueError:
        data = None
    if data is None:
        raise EncoraError(f"Encora answered HTTP {r.status_code}")
    if len(_cache) > 500:
        _cache.clear()
    _cache[ck] = (time.time(), data)
    return data


async def search_shows(q: str, *, transport=None) -> list[dict]:
    data = await _get("/shows/search", {"q": q}, transport=transport)
    rows = data if isinstance(data, list) else (data.get("data") or []) if isinstance(data, dict) else []
    return [{"id": str(r["id"]), "name": r.get("name") or "", "year": r.get("year"),
             "poster": r.get("poster_url") or None}
            for r in rows[:20] if isinstance(r, dict) and r.get("id")]


async def recording(recording_id: str, *, transport=None) -> dict:
    """{show_id, show, tour} of a recording (the number in its Encora link, or
    in the folder's {e-12345})."""
    data = await _get(f"/recording/{int(recording_id)}", transport=transport)
    show_id = ((data.get("metadata") or {}).get("show_id")) if isinstance(data, dict) else None
    if not show_id:
        raise EncoraError("Encora gave no show for that recording")
    return {"show_id": str(show_id), "show": data.get("show") or "", "tour": data.get("tour") or ""}
