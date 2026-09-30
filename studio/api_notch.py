"""What the notch can say: the curated lists behind the Notable Studio /
Director / Cast labels, and which labels a title qualifies for right now.

The lists are upstream's (discovery.py, overridable in
/app/cache/discovery_overrides.json); Studio edits them with the same
functions the admin dashboard's Sash lists view uses.
"""
from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import auth, db, engine, prefs, rules

logger = logging.getLogger("studio")
router = APIRouter(prefix="/studio/api", dependencies=[Depends(auth.require)])
_NO_STORE = {"Cache-Control": "no-store"}


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=_NO_STORE)


@router.get("/notch/lists")
async def lists():
    import config as _cfg
    import discovery
    return _json({"sections": discovery.current_lists(), "writable": discovery.override_writable(),
                  "max_label": discovery.MAX_LABEL_LENGTH, "tmdb": bool(_cfg.SERVER_TMDB_KEY)})


@router.put("/notch/lists")
async def save_lists(request: Request):
    """Body {"changes": {section: [{"name", "label"}] | null}} — null restores the built-in list."""
    import discovery
    import main
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    changes = body.get("changes") if isinstance(body, dict) else None
    if not isinstance(changes, dict) or not changes:
        raise HTTPException(status_code=400, detail="No changes supplied")
    parsed = {}
    for section, entries in changes.items():
        if section not in discovery.SECTIONS:
            raise HTTPException(status_code=400, detail=f"Unknown list {section}")
        try:
            parsed[section] = None if entries is None else discovery.validate_entries(entries)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{section}: {exc}")
    if not discovery.override_writable():
        raise HTTPException(status_code=500, detail="The lists file isn't writable")
    discovery.save_sections(parsed)
    # Posters showing these labels re-render (upstream keys its cache on the lists).
    main._render_assets_signature = main._compute_render_assets_signature()
    logger.info(f"Studio: notch lists saved ({', '.join(sorted(parsed))})")
    return _json({"sections": discovery.current_lists()})


@router.get("/notch/search")
async def search(section: str, q: str):
    import discovery
    import main
    if section not in discovery.SECTIONS:
        raise HTTPException(status_code=400, detail="Unknown list")
    q = q.strip()
    if not q or len(q) > 200:
        raise HTTPException(status_code=400, detail="Type a name to search")
    return _json({"results": await main._sash_tmdb_search(section, q)})


@router.get("/title/{jf_id}/notch")
async def title_notch(jf_id: str):
    """The label this title's poster shows, and every label it qualifies for."""
    row = db.query_one("SELECT * FROM items WHERE jf_id = ?", (jf_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Unknown title")
    if engine.is_stage(row):
        return _json({"available": False, "reason": "Theatre titles have no award or release data: use Notch text."})
    key = rules.title_key(row)
    params = rules.resolve(key, look_override={}).params
    if row["jf_type"] == "Season":
        from . import seasons
        params = await seasons.params_for(row, params)
    import config as _cfg
    style = prefs.get("style_applied")
    url = engine.poster_url(row, style, resolution=500, with_quality=False, access_key=_cfg.ACCESS_KEY or "",
                            extra={**params, "debug": "1"})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0)) as http:
            r = await http.get(url)
        data = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't ask the renderer ({type(exc).__name__})")
    if r.status_code != 200:
        raise HTTPException(status_code=502, detail=str(data.get("detail") or r.status_code))
    off = {s for s in (rules.get_title(key)["style"].get("sash_off") or "").split(",") if s}
    return _json({
        "available": True,
        "shown": data.get("sash"),
        "priority": data.get("sash_priority") or [],
        "candidates": data.get("sash_candidates") or [],
        "off": sorted(off),
        "custom_label": params.get("notch_label") or "",
        "matched": {k: data.get(f"matched_{k}") or [] for k in ("studios", "directors", "cast")},
    })
