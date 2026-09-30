"""Studio API for the Library page and the title editor (under /studio/api)."""
from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from . import auth, candidates, db, engine, prefs, rules

logger = logging.getLogger("studio")
router = APIRouter(prefix="/studio/api", dependencies=[Depends(auth.require)])
_NO_STORE = {"Cache-Control": "no-store"}
MAX_IMAGE = 25 * 1024 * 1024
PREVIEW_WIDTHS = (500, 780, 1000)


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


def _item(jf_id: str) -> dict:
    row = db.query_one("SELECT * FROM items WHERE jf_id = ?", (jf_id,))
    if not row:
        raise HTTPException(status_code=404, detail="That title isn't in Studio's library; run a scan first")
    return row


def _siblings(key: str) -> list[dict]:
    """Every present Jellyfin item sharing this title's rules."""
    return [r for r in db.query("SELECT * FROM items WHERE present = 1") if rules.title_key(r) == key]


def _validate_path(path: str) -> None:
    import art_overrides

    from . import artwork
    if artwork.is_frame(path):
        return
    art_overrides.provider_of(path)


def _validator(key: str):
    """Theatre titles also take StageMedia (or other https) poster links,
    which Studio downloads itself with a public-address check."""
    if not key.startswith("stage:"):
        return _validate_path

    def check(path: str) -> None:
        from . import stage
        from . import artwork
        if path.startswith("custom:") or stage.valid_poster(path) or artwork.is_frame(path):
            return
        raise ValueError("Not an image link Studio can use")
    return check


def _bad(exc: Exception):
    raise HTTPException(status_code=400, detail=str(exc))


# ── Library ─────────────────────────────────────────────────────────────────

@router.get("/library")
async def library():
    summaries = rules.summaries()
    out = []
    for r in db.query("SELECT jf_id, library_name, jf_type, name, year, tmdb_id, manual_tmdb_id, imdb_id, "
                      "stage_show_id, status, last_error, jf_image_tag, pushed_at, added_at FROM items "
                      "WHERE present = 1 AND jf_type != 'Season' ORDER BY name COLLATE NOCASE"):
        key = rules.title_key(r)
        s = summaries.get(key) or {}
        out.append({**r, "title_key": key, "mode": s.get("mode", "auto"), "hands_off": s.get("hands_off", False),
                    "rotation": s.get("rotation", 0), "never": s.get("never", 0), "styled": s.get("styled", False),
                    "reviewed": s.get("reviewed", False)})
    return _json({"items": out, "libraries": sorted({r["library_name"] for r in out})})


# ── One title ───────────────────────────────────────────────────────────────

def _media_type(row: dict) -> str:
    return "tv" if row["jf_type"] in ("Series", "Season") else "movie"


def _uploads_for(key: str) -> list[dict]:
    from . import uploads
    return [{"path": u["path"], "kind": u["kind"], "name": u["name"], "own_title": u["own_title"],
             "added_at": u["added_at"]} for u in uploads.for_title(key)]


def _parent_brief(jf_id: str | None) -> dict | None:
    if not jf_id:
        return None
    p = db.query_one("SELECT jf_id, name, year, jf_image_tag FROM items WHERE jf_id = ?", (jf_id,))
    return p


def _title_payload(row: dict) -> dict:
    key = rules.title_key(row)
    t = rules.get_title(key)
    res = rules.resolve(key)
    return {
        "item": row, "title_key": key, "media_type": _media_type(row), "stage": engine.is_stage(row),
        "tmdb_id": row.get("manual_tmdb_id") or row.get("tmdb_id"),
        "title": t, "looks": rules.looks(key),
        "never": {k: sorted(v) for k, v in rules.never(key).items()},
        "today_look_id": res.look_id, "upcoming": res.upcoming, "skip": res.skip,
        "uploads": _uploads_for(key),
        "siblings": [{"jf_id": s["jf_id"], "library_name": s["library_name"], "name": s["name"]}
                     for s in _siblings(key)],
        # A show lists its seasons; a season lists its show's (itself included).
        "seasons": [{"jf_id": s["jf_id"], "number": s["season_number"], "name": s["name"], "status": s["status"],
                     "jf_image_tag": s["jf_image_tag"]}
                    for s in db.query("SELECT * FROM items WHERE parent_jf_id = ? AND present = 1 "
                                      "ORDER BY season_number", (row.get("parent_jf_id") or row["jf_id"],))],
        "parent": row.get("parent_jf_id"),
        "parent_item": _parent_brief(row.get("parent_jf_id")),
        "art": _art_payload(key),
    }


def _art_payload(key: str) -> dict:
    from . import artwork
    rules_ = artwork.library_rules()
    return {k: {**artwork.choice(key, k), "managed": artwork.managed(key, k), "library_on": rules_[k]["enabled"]}
            for k in artwork.KINDS} | {"rules": rules_}


@router.get("/title/{jf_id}")
async def title(jf_id: str):
    return _json(_title_payload(_item(jf_id)))


@router.get("/title/{jf_id}/candidates")
async def title_candidates(jf_id: str, force: bool = False):
    row = _item(jf_id)
    if engine.is_stage(row):
        from . import stage
        try:
            found = await stage.posters(row["stage_show_id"], force=force)
        except stage.StageError as exc:
            return _json({"candidates": {"posters": [], "backdrops": [], "logos": []}, "auto": None,
                          "stage": True, "error": str(exc)})
        items = [{"path": u, "thumb": f"/studio/api/stage-thumb?url={quote(u, safe='')}", "language": None,
                  "provider": "stagemedia"} for u in found]
        return _json({"candidates": {"posters": items, "backdrops": [], "logos": []}, "stage": True,
                      "auto": {"poster": {"path": found[0], "kind": "poster"} if found else None, "logo": None},
                      "title": stage.show_name(row["name"])})
    tmdb_id = row.get("manual_tmdb_id") or row.get("tmdb_id")
    if not tmdb_id:
        return _json({"candidates": {"posters": [], "backdrops": [], "logos": []}, "auto": None,
                      "error": "No TMDB match yet: link one to see artwork."})
    try:
        result = await candidates.for_title(_media_type(row), tmdb_id, force=force)
        if row["jf_type"] == "Season":
            from . import seasons
            own = await seasons.season_posters(tmdb_id, int(row.get("season_number") or 0))
            result = {**result, "season": True,
                      "candidates": {**result["candidates"], "posters": own + result["candidates"]["posters"]},
                      "auto": {**result["auto"], "poster": {"path": own[0]["path"], "kind": "poster"} if own else result["auto"]["poster"]}}
        return _json(result)
    except Exception as exc:
        logger.warning(f"Studio candidates for {jf_id}: {exc}")
        return _json({"candidates": {"posters": [], "backdrops": [], "logos": []}, "auto": None,
                      "error": f"Couldn't load artwork: {exc}"})


@router.get("/stage-thumb")
async def stage_thumb(url: str, w: int = 342):
    """A small copy of a theatre poster (StageMedia's need the API key, which
    the browser never sees).  Only images Studio already knows of: a show's
    StageMedia list or a saved look."""
    from . import stage
    known = any(url in found for _, found in stage._lists.values()) or bool(
        db.query_one("SELECT 1 FROM looks WHERE poster = ? AND title_key LIKE 'stage:%'", (url,)))
    if not known:
        raise HTTPException(status_code=404, detail="Unknown image")
    try:
        data = await stage.thumbnail(url, max(120, min(w, 800)))
    except stage.StageError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.put("/title/{jf_id}")
async def update_title(jf_id: str, request: Request):
    row, body = _item(jf_id), await _body(request)
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    try:
        if "mode" in body:
            rules.set_mode(key, str(body["mode"]), body.get("pinned_look_id"))
        if "hands_off" in body:
            rules.set_hands_off(key, bool(body["hands_off"]))
        if "style" in body:
            rules.set_title_style(key, body["style"])
        if "reviewed" in body:
            rules.mark_reviewed(key, bool(body["reviewed"]))
    except ValueError as exc:
        _bad(exc)
    return _json(_title_payload(row))


@router.post("/title/{jf_id}/reset")
async def reset_title(jf_id: str):
    row = _item(jf_id)
    rules.reset_title(rules.title_key(row))
    return _json(_title_payload(row))


@router.post("/title/{jf_id}/looks")
async def add_look(jf_id: str, request: Request):
    row, body = _item(jf_id), await _body(request)
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    try:
        look = rules.add_look(key, body, _validator(key))
        if body.get("pin"):
            rules.set_mode(key, "pinned", look["look_id"])
        elif body.get("in_rotation") and rules.get_title(key)["mode"] != "rotation":
            rules.set_mode(key, "rotation")
    except ValueError as exc:
        _bad(exc)
    return _json({**_title_payload(row), "look": look})


@router.put("/looks/{look_id}")
async def update_look(look_id: int, request: Request):
    body = await _body(request)
    existing = rules.get_look(look_id)
    if not existing:
        raise HTTPException(status_code=404, detail="No such look")
    try:
        look = rules.update_look(look_id, body, _validator(existing["title_key"]))
    except KeyError:
        raise HTTPException(status_code=404, detail="No such look")
    except ValueError as exc:
        _bad(exc)
    return _json({"look": look})


@router.put("/title/{jf_id}/looks-logo")
async def logo_for_all_looks(jf_id: str, request: Request):
    """Body {logo}: the same logo on every look of the title."""
    row, body = _item(jf_id), await _body(request)
    key = rules.title_key(row)
    logo = str(body.get("logo") or "")
    try:
        for look in rules.looks(key):
            rules.update_look(look["look_id"], {"logo": logo}, _validator(key))
    except ValueError as exc:
        _bad(exc)
    return _json(_title_payload(row))


@router.delete("/looks/{look_id}")
async def delete_look(look_id: int):
    rules.delete_look(look_id)
    return _json({"deleted": look_id})


@router.put("/title/{jf_id}/never")
async def set_never(jf_id: str, request: Request):
    row, body = _item(jf_id), await _body(request)
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    try:
        rules.set_never(key, str(body.get("kind")), str(body.get("ref") or ""), bool(body.get("on")))
    except ValueError as exc:
        _bad(exc)
    return _json(_title_payload(row))


# ── Your own images ─────────────────────────────────────────────────────────

def _upload_kind(kind: str) -> str:
    from . import uploads
    if kind not in uploads.KINDS:
        raise HTTPException(status_code=400, detail="kind must be poster, backdrop or logo")
    return kind


async def _store(data: bytes, kind: str) -> str:
    import art_overrides

    from . import uploads
    try:
        return await asyncio.to_thread(art_overrides.store_custom_image, data, kind=uploads.STORE_KIND[kind])
    except ValueError as exc:
        _bad(exc)


@router.post("/title/{jf_id}/image")
async def upload_image(jf_id: str, request: Request, kind: str = "poster", name: str = ""):
    """One image file as the body.  It joins the title's own images (kept until
    you delete it); using it is a separate step."""
    from . import uploads
    row, kind = _item(jf_id), _upload_kind(kind)
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_IMAGE:
            raise HTTPException(status_code=413, detail="The image is over 25 MB")
        chunks.append(chunk)
    path = await _store(b"".join(chunks), kind)
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    return _json({"path": path, "upload": uploads.add(key, kind, path, name)})


@router.post("/title/{jf_id}/image-link")
async def image_link(jf_id: str, request: Request):
    from . import uploads
    row, body = _item(jf_id), await _body(request)
    kind = _upload_kind(str(body.get("kind") or "poster"))
    url = str(body.get("url") or "").strip()
    if not url or len(url) > 2048:
        raise HTTPException(status_code=400, detail="Paste an image link")
    import art_overrides
    import main
    try:
        data = await art_overrides.download_custom_url(main._HTTP_CLIENT, url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't use that link: {exc}")
    path = await _store(data, kind)
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    return _json({"path": path, "upload": uploads.add(key, kind, path, url.rsplit("/", 1)[-1][:80])})


@router.put("/title/{jf_id}/uploads")
async def update_upload(jf_id: str, request: Request):
    """Body {path, own_title}: whether an uploaded poster already has the title on it."""
    from . import uploads
    row, body = _item(jf_id), await _body(request)
    uploads.set_own_title(rules.title_key(row), str(body.get("path") or ""), bool(body.get("own_title")))
    return _json(_title_payload(row))


@router.delete("/title/{jf_id}/uploads")
async def delete_upload(jf_id: str, path: str):
    from . import uploads
    row = _item(jf_id)
    key = rules.title_key(row)
    if db.query_one("SELECT 1 FROM looks WHERE title_key = ? AND (poster = ? OR logo = ?)", (key, path, path)):
        raise HTTPException(status_code=400, detail="A look still uses this image: remove it from the rotation "
                                                    "or pin something else first")
    uploads.remove(key, path)
    return _json(_title_payload(row))


# ── Preview and push ────────────────────────────────────────────────────────

def _draft_look(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return {
            "look_id": d.get("look_id"),
            "poster": str(d.get("poster") or ""), "crop": rules.clean_crop(d.get("crop")),
            "own_title": bool(d.get("own_title")), "logo": str(d.get("logo") or ""),
            "colors": rules.clean_colors(d.get("colors") or {}), "style": rules.clean_style(d.get("style") or {}),
        }
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"Bad preview look: {exc}")


@router.get("/preview/{jf_id}")
async def preview(jf_id: str, look: str | None = None, look_id: int | None = None, w: int = 500,
                  title_style: str | None = None, style: str = "applied"):
    """The poster exactly as Jellyfin would get it: today's pick, a saved look,
    or an unsaved one from the editor (*look* as JSON).  style=draft renders
    with the global style being edited instead of the applied one."""
    row = _item(jf_id)
    key = rules.title_key(row)
    override = _draft_look(look)
    if override is None and look_id is not None:
        override = rules.get_look(look_id)
        if not override or override["title_key"] != key:
            raise HTTPException(status_code=404, detail="No such look")
    draft_style = None
    if title_style is not None:
        try:
            draft_style = rules.clean_style(json.loads(title_style))
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=f"Bad style: {exc}")
    res = rules.resolve(key, look_override=override, title_style=draft_style)
    if res.skip:   # hands off: still show what Studio would make
        res = rules.resolve(key, look_override={}, title_style=draft_style)
    params = res.params
    if row["jf_type"] == "Season":
        from . import seasons
        params = await seasons.params_for(row, params)
    draft = db.get_setting("style_draft") if style == "draft" else None
    style = draft or prefs.get("style_applied")
    import config as _cfg
    if not engine.is_stage(row):
        from . import artwork
        try:
            params = await artwork.realize(params)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Couldn't get that frame: {exc}")
    url = engine.poster_url(row, style, resolution=w if w in PREVIEW_WIDTHS else 500,
                            with_quality=engine._style_uses_quality(style), access_key=_cfg.ACCESS_KEY or "",
                            extra=params)
    try:
        if engine.is_stage(row):
            from . import stage
            data, ctype = await stage.render(row, style, params, w if w in PREVIEW_WIDTHS else 500)
        else:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=5.0)) as http:
                data, ctype = await engine.render(http, url)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or type(exc).__name__)
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=600"})


@router.post("/title/{jf_id}/push")
async def push(jf_id: str):
    """Send this title's poster(s) to Jellyfin now."""
    row = _item(jf_id)
    if not prefs.get("uploads_enabled"):
        raise HTTPException(status_code=400, detail="Uploads are off in Settings, so nothing can be sent")
    if engine._run_lock.locked():
        raise HTTPException(status_code=409, detail="A run is in progress; try again when it finishes")
    ids = [s["jf_id"] for s in _siblings(rules.title_key(row))] or [jf_id]
    run_id = await engine.run(trigger="manual", dry_run=False, item_ids=ids)
    run = db.query_one("SELECT status, counts, message FROM runs WHERE id = ?", (run_id,))
    items = db.query("SELECT name, action, detail FROM run_items WHERE run_id = ?", (run_id,))
    return _json({"run_id": run_id, "status": run["status"], "counts": json.loads(run["counts"]),
                  "message": run["message"], "items": items})


# ── Jellyfin's other images: Backdrop, Logo, Thumb ──────────────────────────

@router.put("/title/{jf_id}/art/{kind}")
async def set_art(jf_id: str, kind: str, request: Request):
    """Body {mode: auto|pinned|keep, path, crop}."""
    from . import artwork
    row, body = _item(jf_id), await _body(request)
    key = rules.title_key(row)
    path = str(body.get("path") or "")
    try:
        if "logo" in body:
            logo = str(body.get("logo") or "")
            if logo not in ("", "text", "none"):
                _validate_path(logo)
            artwork.set_logo(key, kind, logo)
        if "mode" not in body:
            return _json(_title_payload(row))
        if path and not path.startswith("jf-chapter:"):
            _validator(key)(path)
        artwork.set_choice(key, kind, str(body.get("mode") or "auto"), path, rules.clean_crop(body.get("crop")))
    except ValueError as exc:
        _bad(exc)
    return _json(_title_payload(row))


@router.get("/preview-art/{jf_id}/{kind}")
async def preview_art(jf_id: str, kind: str, mode: str | None = None, path: str = "", crop: str = "",
                      style: str = "applied", landscape: bool = False, logo: str | None = None):
    """What Studio would send as this item's Backdrop / Logo / Thumb (the saved
    choice, or an unsaved one given as mode/path/crop)."""
    from . import artwork
    if kind not in artwork.KINDS:
        raise HTTPException(status_code=400, detail="Unknown image type")
    row = _item(jf_id)
    draft = {"mode": mode, "path": path, "crop": crop} if mode else None
    if logo is not None:
        draft = {**(draft or {}), "logo": logo}
    try:
        style_str = (db.get_setting("style_draft") or None) if style == "draft" else None
        res = await artwork.resolve(row, kind, draft=draft, style_str=style_str, landscape=landscape)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc) or type(exc).__name__)
    if res is None:
        raise HTTPException(status_code=404, detail="Nothing to send: Jellyfin's stays")
    data, ctype = res
    return Response(data, media_type=ctype, headers={"Cache-Control": "private, max-age=600"})


@router.get("/title/{jf_id}/frames")
async def frames(jf_id: str):
    """Frames: TMDB's episode stills for shows, then the chapter images
    Jellyfin extracted from your files (a film's, or a show's first episodes')."""
    row = _item(jf_id)
    jf = engine.shared_client()
    targets = [row["jf_id"]]
    if row["jf_type"] == "Series":
        try:
            eps = [d async for d in jf._paged(f"/Shows/{row['jf_id']}/Episodes", Fields="Chapters")]
        except Exception:
            eps = []
        targets = [e["Id"] for e in eps[:6]]
    out = []
    for item_id in targets:
        try:
            data = await jf._get("/Items", Ids=item_id, Fields="Chapters")
        except Exception:
            continue
        for it in data.get("Items") or []:
            for i, ch in enumerate(it.get("Chapters") or []):
                if ch.get("ImageTag"):
                    secs = int((ch.get("StartPositionTicks") or 0) / 10_000_000)
                    out.append({"path": f"jf-chapter:{it['Id']}:{i}", "provider": "frame", "language": None,
                                "thumb": f"/studio/api/thumb/{it['Id']}?type=Chapter/{i}&h=300&tag={ch['ImageTag']}",
                                "name": f"{it.get('Name', '')[:30]} · {secs // 60}:{secs % 60:02d}"})
    out = out[:120]
    # Official episode stills from TMDB for shows (a season's own, or the first seasons').
    tmdb_id = row.get("manual_tmdb_id") or row.get("tmdb_id")
    if tmdb_id and row["jf_type"] in ("Series", "Season") and not engine.is_stage(row):
        out = await _episode_stills(tmdb_id, [int(row["season_number"] or 0)] if row["jf_type"] == "Season" else [1, 2, 3]) + out
    return _json({"frames": out})


async def _episode_stills(tmdb_id: str, seasons: list[int]) -> list[dict]:
    import config as _cfg
    import main
    if not _cfg.SERVER_TMDB_KEY:
        return []
    out = []
    for n in seasons:
        try:
            r = await main._proxy_tmdb_get(f"https://api.themoviedb.org/3/tv/{tmdb_id}/season/{n}", {"api_key": _cfg.SERVER_TMDB_KEY})
        except Exception:
            continue
        if r.status_code != 200:
            continue
        for ep in r.json().get("episodes") or []:
            if ep.get("still_path"):
                out.append({"path": ep["still_path"], "provider": "tmdb", "language": None,
                            "thumb": f"https://image.tmdb.org/t/p/w300{ep['still_path']}",
                            "name": f"S{n}E{ep.get('episode_number')} · {(ep.get('name') or '')[:28]}"})
    return out


@router.post("/title/{jf_id}/frames/import")
async def import_frame(jf_id: str, request: Request):
    """A frame into your images (as a backdrop), so it can be framed into a
    poster, pinned or rotated like any image."""
    from . import artwork, uploads
    row, body = _item(jf_id), await _body(request)
    path = str(body.get("path") or "")
    # A Jellyfin chapter image, or a TMDB episode still (a TMDB image path).
    if not (path.startswith("jf-chapter:") or (path.startswith("/") and "/" not in path[1:] and len(path) < 80)):
        raise HTTPException(status_code=400, detail="Not a frame")
    try:
        data = await artwork.fetch(path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    stored = await _store(data, "backdrop")
    key = rules.title_key(row)
    rules.ensure_title(key, row["name"])
    return _json({"path": stored, "upload": uploads.add(key, "backdrop", stored, str(body.get("name") or "Frame")[:80])})


# ── Bulk actions (the Library's selection) ──────────────────────────────────

BULK_ACTIONS = ("hands_off", "manage", "reset", "reviewed", "unreviewed", "push")
_bulk_tasks: set[asyncio.Task] = set()


@router.post("/bulk")
async def bulk(request: Request):
    body = await _body(request)
    ids, action = body.get("jf_ids"), body.get("action")
    if action not in BULK_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) for i in ids) or len(ids) > 5000:
        raise HTTPException(status_code=400, detail="jf_ids must be a list of Jellyfin ids")
    rows = [r for r in (db.query_one("SELECT * FROM items WHERE jf_id = ?", (i,)) for i in ids) if r]
    keys = {rules.title_key(r): r["name"] for r in rows}
    if action == "push":
        if not prefs.get("uploads_enabled"):
            raise HTTPException(status_code=400, detail="Uploads are off in Settings, so nothing can be sent")
        if engine._run_lock.locked():
            raise HTTPException(status_code=409, detail="A run is in progress; try again when it finishes")
        every = sorted({s["jf_id"] for k in keys for s in _siblings(k)} | {r["jf_id"] for r in rows})
        task = asyncio.create_task(engine.run(trigger="manual", dry_run=False, item_ids=every))
        _bulk_tasks.add(task)
        task.add_done_callback(_bulk_tasks.discard)
        return _json({"started": True, "items": len(every)})
    for key, name in keys.items():
        if action == "reset":
            rules.reset_title(key)
            continue
        rules.ensure_title(key, name)
        if action in ("hands_off", "manage"):
            rules.set_hands_off(key, action == "hands_off")
        else:
            rules.mark_reviewed(key, action == "reviewed")
    return _json({"titles": len(keys)})


# ── Matching titles Jellyfin couldn't ───────────────────────────────────────

@router.get("/tmdb/search")
async def tmdb_search(q: str, type: str = "movie"):
    q = q.strip()
    if not q or len(q) > 200:
        raise HTTPException(status_code=400, detail="Type a title to search")
    import config as _cfg
    import main
    if not _cfg.SERVER_TMDB_KEY:
        raise HTTPException(status_code=400, detail="PostersPlus has no TMDB key")
    kind = "tv" if type == "tv" else "movie"
    resp = await main._proxy_tmdb_get(f"https://api.themoviedb.org/3/search/{kind}",
                                      {"api_key": _cfg.SERVER_TMDB_KEY, "query": q, "include_adult": "false"})
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"TMDB search returned {resp.status_code}")
    out = []
    for r in (resp.json().get("results") or [])[:12]:
        date = r.get("release_date") or r.get("first_air_date") or ""
        out.append({"tmdb_id": str(r["id"]), "title": r.get("title") or r.get("name") or "", "year": date[:4],
                    "thumb": f"https://image.tmdb.org/t/p/w154{r['poster_path']}" if r.get("poster_path") else None,
                    "overview": (r.get("overview") or "")[:200]})
    return _json({"results": out})


@router.put("/items/{jf_id}/match")
async def match(jf_id: str, request: Request):
    row, body = _item(jf_id), await _body(request)
    tmdb_id = str(body.get("tmdb_id") or "").strip()
    if tmdb_id and not tmdb_id.isdigit():
        raise HTTPException(status_code=400, detail="tmdb_id must be a number")
    status = engine.NEW if (tmdb_id or row.get("tmdb_id") or row.get("imdb_id")) else engine.NEEDS_MATCH
    db.execute("UPDATE items SET manual_tmdb_id = ?, status = ?, pushed_hash = NULL WHERE jf_id = ?",
               (tmdb_id or None, status, jf_id))
    return _json(_title_payload(_item(jf_id)))
