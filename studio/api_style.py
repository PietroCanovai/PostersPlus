"""The global poster style: an applied style Jellyfin gets, and a draft you
edit and preview on your own titles until you press Apply."""
from __future__ import annotations

import asyncio
import random
from urllib.parse import parse_qsl, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import auth, db, engine, prefs

router = APIRouter(prefix="/studio/api", dependencies=[Depends(auth.require)])
_NO_STORE = {"Cache-Control": "no-store"}
SAMPLE_COUNT = 6
_tasks: set[asyncio.Task] = set()


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers=_NO_STORE)


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="Body must be JSON")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


def draft() -> str | None:
    return db.get_setting("style_draft")


def _sash_info(style: str) -> dict:
    """The sash order a style gives, and every slot there is, via the renderer's own parser."""
    import config as _cfg
    import main
    value = dict(parse_qsl(style)).get("sash_priority")
    active = main._parse_sash_priority(value)
    # The real labels: ALL_PRIORITY_SLOTS adds legacy combined tokens that repeat them.
    return {"active": [s for s in active if s in _cfg.SASH_PRIORITY], "all": list(_cfg.SASH_PRIORITY)}


# Settings the Style page has controls for; their renderer defaults are sent
# along so a control shows the true value when the style doesn't set it.
CONTROLLED = ("logo_max_w_ratio", "logo_max_h_ratio", "logo_bottom_ratio", "logo_bottom_anchor", "logo_priority",
              "top_gradient", "bottom_gradient", "vignette_poster_color_bottom", "show_award_sash", "sash_mode",
              "sash_badge_style", "sash_badge_pos", "sash_badge_size_w", "sash_badge_size_h",
              "rating_display_mode", "badge_display_mode", "fallback_bg_style", "label_font",
              # The editor's shared controls (colours default to automatic).
              "logo_color", "logo_color_mode", "fade_color", "tint_color", "notch_text_color", "notch_label")


# The Thumb's own controls (landscape renders only; portraits never get them).
THUMB_CONTROLLED = ("landscape_art", "landscape_logo_pos", "landscape_info_pos", "landscape_info_scale",
                    "landscape_badge_pos", "landscape_badge_scale", "landscape_hide_genre", "landscape_hide_year",
                    "landscape_hide_rating", "landscape_score_out_of_10", "landscape_vignette_poster_color_bottom",
                    "landscape_vignette_top")


def _defaults() -> dict:
    import main
    cfg = main.build_request_config({})
    out = {}
    lcfg = main.build_request_config({"shape": "landscape"})
    for k in THUMB_CONTROLLED:
        name = k[len("landscape_"):] if k[len("landscape_"):] in main._LANDSCAPE_SPLIT_PARAMS else k
        v = getattr(lcfg, name, None)
        out[k] = ("true" if v else "false") if isinstance(v, bool) else ("" if v is None else str(v))
    for k in CONTROLLED:
        v = getattr(cfg, k, None)
        if isinstance(v, tuple):   # a parsed colour
            v = "%02x%02x%02x" % v
        out[k] = ("true" if v else "false") if isinstance(v, bool) else ("" if v is None else str(v))
    return out


def _payload() -> dict:
    applied, d = prefs.get("style_applied"), draft()
    current = d if d is not None else applied
    return {
        "defaults": _defaults(),
        "applied": applied, "draft": d, "has_draft": d is not None,
        "can_undo": bool(db.get_setting("style_previous")),
        "params": dict(parse_qsl(current, keep_blank_values=True)),
        "applied_params": dict(parse_qsl(applied, keep_blank_values=True)),
        "sash": _sash_info(current),
        "samples": samples(),
    }


def samples(reroll: bool = False) -> list[dict]:
    """A handful of your own titles to preview a style on: films and shows,
    kept until you ask for others."""
    rows = db.query("SELECT jf_id, name, jf_type, library_name FROM items WHERE present = 1 "
                    "AND status IN ('ok', 'new') ORDER BY jf_id")
    by_id = {r["jf_id"]: r for r in rows}
    saved = [i for i in (db.get_setting("style_samples") or []) if i in by_id]
    if reroll or len(saved) < min(SAMPLE_COUNT, len(rows)):
        rng = random.Random()
        shows = [r["jf_id"] for r in rows if r["jf_type"] == "Series"]
        films = [r["jf_id"] for r in rows if r["jf_type"] != "Series"]
        picked = rng.sample(shows, min(2, len(shows))) + rng.sample(films, min(SAMPLE_COUNT - 2, len(films)))
        if len(picked) < SAMPLE_COUNT:
            rest = [r["jf_id"] for r in rows if r["jf_id"] not in picked]
            picked += rng.sample(rest, min(SAMPLE_COUNT - len(picked), len(rest)))
        saved = picked
        db.set_setting("style_samples", saved)
    return [{"jf_id": i, "name": by_id[i]["name"], "library_name": by_id[i]["library_name"]} for i in saved]


@router.get("/style")
async def get_style():
    return _json(_payload())


@router.put("/style")
async def put_style(request: Request):
    """Body {params: {name: value}}: the whole draft style.  Saving the applied
    style's exact settings drops the draft."""
    body = await _body(request)
    params = body.get("params")
    if not isinstance(params, dict):
        raise HTTPException(status_code=400, detail="params must be an object")
    pairs = {str(k): str(v) for k, v in params.items() if v is not None and str(v) != ""}
    try:
        cleaned = prefs.clean_style(urlencode(pairs))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if dict(parse_qsl(cleaned)) == dict(parse_qsl(prefs.get("style_applied"))):
        db.delete_setting("style_draft")
    else:
        db.set_setting("style_draft", cleaned)
    return _json(_payload())


@router.post("/style/import")
async def import_style(request: Request):
    body = await _body(request)
    try:
        cleaned = prefs.clean_style(str(body.get("url") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    db.set_setting("style_draft", cleaned)
    return _json(_payload())


@router.post("/style/discard")
async def discard_style():
    db.delete_setting("style_draft")
    return _json(_payload())


@router.post("/style/apply")
async def apply_style(request: Request):
    """Make the draft the style Jellyfin gets, and (optionally) run the library now."""
    body = await _body(request)
    d = draft()
    if d is None:
        raise HTTPException(status_code=400, detail="There are no unapplied changes")
    if body.get("run") and engine._run_lock.locked():
        raise HTTPException(status_code=409, detail="A run is in progress; apply again when it finishes")
    db.set_setting("style_previous", prefs.get("style_applied"))
    prefs.set("style_applied", d)
    db.delete_setting("style_draft")
    started = False
    if body.get("run"):
        task = asyncio.create_task(engine.run(trigger="apply", dry_run=False))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        started = True
    return _json({**_payload(), "run_started": started, "uploads_enabled": bool(prefs.get("uploads_enabled"))})


@router.post("/style/undo")
async def undo_style():
    """Back to the style that was applied before the last Apply (as a draft, to apply again)."""
    prev = db.get_setting("style_previous")
    if not prev:
        raise HTTPException(status_code=400, detail="No earlier style to go back to")
    db.set_setting("style_draft", prev)
    return _json(_payload())


@router.post("/style/samples")
async def reroll_samples():
    return _json({"samples": samples(reroll=True)})
