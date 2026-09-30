"""Studio's HTTP API, under /studio/api.  Every route but login needs a session."""
from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse

from . import auth, db, engine, prefs
from .jellyfin import Client, JellyfinError

router = APIRouter()
api = APIRouter(prefix="/studio/api", dependencies=[Depends(auth.require)])

WEB_DIR = os.path.join(os.path.dirname(__file__), "web")
_NO_STORE = {"Cache-Control": "no-store"}
REPO = "PietroCanovai/PostersPlus"
BRANCH = "studio"


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


def _secret_hint(value: str) -> str:
    return "" if not value else ("…" + value[-4:] if len(value) > 6 else "•" * len(value))


# ── Page and login (no session needed) ──────────────────────────────────────

@router.get("/studio", include_in_schema=False)
async def page():
    return FileResponse(os.path.join(WEB_DIR, "index.html"),
                        headers={**_NO_STORE, "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})


@router.post("/studio/api/login")
async def login(request: Request):
    body = await _body(request)
    await auth.login(request, str(body.get("key") or ""))
    resp = _json({"ok": True})
    auth.set_session(request, resp)
    return resp


@router.post("/studio/api/logout")
async def logout():
    resp = _json({"ok": True})
    auth.clear_session(resp)
    return resp


@router.get("/studio/api/session")
async def session(request: Request):
    import admin
    return _json({"logged_in": auth.valid_token(request.cookies.get(auth.COOKIE)),
                  "enabled": admin.enabled()})


# ── Version and updates ─────────────────────────────────────────────────────

_latest: dict = {"checked": 0.0, "sha": None, "date": None, "message": None}


async def _latest_commit() -> dict:
    if time.time() - _latest["checked"] < 3600:
        return _latest
    _latest["checked"] = time.time()
    try:
        async with httpx.AsyncClient(timeout=8.0) as http:
            r = await http.get(f"https://api.github.com/repos/{REPO}/commits/{BRANCH}",
                               headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            c = r.json()
            _latest.update(sha=c["sha"], date=c["commit"]["committer"]["date"],
                           message=c["commit"]["message"].splitlines()[0])
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return _latest


def _version() -> dict:
    return {"commit": os.environ.get("STUDIO_GIT_COMMIT", "unknown"),
            "built": os.environ.get("STUDIO_BUILD_DATE", "unknown")}


@api.get("/version")
async def version():
    mine, latest = _version(), await _latest_commit()
    update = bool(latest["sha"] and mine["commit"] not in ("unknown", latest["sha"]))
    return _json({**mine, "latest": latest["sha"], "latest_date": latest["date"],
                  "latest_message": latest["message"], "update_available": update,
                  "update_command": "bash ~/docker/postersplus/update.sh"})


# ── Status (the Activity page) ──────────────────────────────────────────────

@api.get("/status")
async def status():
    by_status = {r["status"]: r["n"] for r in db.query(
        "SELECT status, COUNT(*) AS n FROM items WHERE present = 1 GROUP BY status")}
    runs = db.query("SELECT * FROM runs ORDER BY id DESC LIMIT 10")
    for r in runs:
        r["counts"] = json.loads(r["counts"] or "{}")
    nxt = engine.next_run_at()
    return _json({
        "configured": bool(prefs.get("jellyfin_url") and prefs.get("jellyfin_api_key")),
        "uploads_enabled": bool(prefs.get("uploads_enabled")),
        "schedule_enabled": bool(prefs.get("schedule_enabled")),
        "schedule_time": prefs.get("schedule_time"),
        "style_draft": db.get_setting("style_draft") is not None,
        "next_run": nxt.isoformat(timespec="minutes") if nxt else None,
        "now": datetime.now().isoformat(timespec="minutes"),
        "last_scan_at": db.get_setting("last_scan_at"),
        "items": by_status,
        "total": sum(by_status.values()),
        "progress": engine.progress.as_dict(),
        "runs": runs,
        "version": _version(),
    })


@api.get("/runs/{run_id}")
async def run_detail(run_id: int):
    run = db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="No such run")
    run["counts"] = json.loads(run["counts"] or "{}")
    run["items"] = db.query("SELECT jf_id, name, action, detail, at FROM run_items WHERE run_id = ? "
                            "ORDER BY CASE action WHEN 'error' THEN 0 WHEN 'reverted' THEN 1 "
                            "WHEN 'uploaded' THEN 2 WHEN 'would_upload' THEN 3 ELSE 4 END, name", (run_id,))
    return _json(run)


@api.get("/items")
async def items(status: str = ""):
    sql = ("SELECT jf_id, library_name, jf_type, name, year, tmdb_id, imdb_id, status, last_error, "
           "pushed_at, revert_count FROM items WHERE present = 1")
    args: list = []
    if status:
        sql += " AND status = ?"
        args.append(status)
    return _json({"items": db.query(sql + " ORDER BY name COLLATE NOCASE", args)})


# ── Runs ────────────────────────────────────────────────────────────────────

_tasks: set[asyncio.Task] = set()


@api.post("/run")
async def start_run(request: Request):
    body = await _body(request)
    if engine._run_lock.locked():
        raise HTTPException(status_code=409, detail="A run is already in progress")
    ids = body.get("item_ids")
    if ids is not None and not (isinstance(ids, list) and all(isinstance(i, str) for i in ids)):
        raise HTTPException(status_code=400, detail="item_ids must be a list of Jellyfin ids")
    task = asyncio.create_task(engine.run(trigger="manual", dry_run=bool(body.get("dry_run")),
                                          item_ids=ids, force=bool(body.get("force"))))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    await asyncio.sleep(0.05)
    return _json({"started": True, "progress": engine.progress.as_dict()})


@api.post("/run/cancel")
async def cancel_run():
    engine.progress.cancel = True
    return _json({"cancelling": engine.progress.running})


# ── Settings ────────────────────────────────────────────────────────────────

def _server_max_resolution() -> int:
    """PostersPlus's own ceiling (MAX_POSTER_RESOLUTION): a bigger request is
    quietly served at this size."""
    try:
        import config as _cfg
        return int(_cfg.MAX_POSTER_RESOLUTION)
    except Exception:
        return 2000


def _settings_payload() -> dict:
    return {
        "server_max_resolution": _server_max_resolution(),
        "jellyfin_url": prefs.get("jellyfin_url"),
        "jellyfin_api_key_set": bool(prefs.get("jellyfin_api_key")),
        "jellyfin_api_key_hint": _secret_hint(prefs.get("jellyfin_api_key")),
        "uploads_enabled": bool(prefs.get("uploads_enabled")),
        "schedule_enabled": bool(prefs.get("schedule_enabled")),
        "schedule_time": prefs.get("schedule_time"),
        "seasons_enabled": bool(prefs.get("seasons_enabled")),
        "stagemedia_key_set": bool(prefs.get("stagemedia_key")),
        "stagemedia_key_hint": _secret_hint(prefs.get("stagemedia_key") or ""),
        "resolution": int(prefs.get("resolution") or 1000),
        "resolutions": list(prefs.RESOLUTIONS),
        "style_applied": prefs.get("style_applied"),
        "timezone": time.strftime("%Z"),
    }


@api.get("/settings")
async def get_settings():
    return _json(_settings_payload())


@api.put("/settings")
async def put_settings(request: Request):
    body = await _body(request)
    if "jellyfin_url" in body:
        url = str(body["jellyfin_url"] or "").strip().rstrip("/")
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="The Jellyfin address must start with http:// or https://")
        prefs.set("jellyfin_url", url)
    if body.get("jellyfin_api_key"):
        prefs.set("jellyfin_api_key", str(body["jellyfin_api_key"]).strip())
    if "stagemedia_key" in body:
        prefs.set("stagemedia_key", str(body["stagemedia_key"] or "").strip())
    if "uploads_enabled" in body:
        prefs.set("uploads_enabled", bool(body["uploads_enabled"]))
    if "seasons_enabled" in body:
        prefs.set("seasons_enabled", bool(body["seasons_enabled"]))
    if "schedule_time" in body:
        if not prefs.valid_time(str(body["schedule_time"])):
            raise HTTPException(status_code=400, detail="Time must be HH:MM, e.g. 04:00")
        prefs.set("schedule_time", str(body["schedule_time"]))
    if "schedule_enabled" in body:
        on = bool(body["schedule_enabled"])
        if on and not prefs.get("schedule_enabled"):
            engine.mark_schedule_enabled()
        prefs.set("schedule_enabled", on)
    if "resolution" in body:
        if int(body["resolution"]) not in prefs.RESOLUTIONS:
            raise HTTPException(status_code=400, detail="Unsupported resolution")
        prefs.set("resolution", int(body["resolution"]))
    return _json(_settings_payload())


@api.post("/jellyfin/test")
async def test_jellyfin(request: Request):
    body = await _body(request)
    url = str(body.get("jellyfin_url") or prefs.get("jellyfin_url") or "").strip()
    key = str(body.get("jellyfin_api_key") or prefs.get("jellyfin_api_key") or "").strip()
    try:
        async with Client(url, key) as jf:
            info = await jf.server_info()
            libs = await jf.libraries()
    except JellyfinError as exc:
        return _json({"ok": False, "error": str(exc)})
    return _json({"ok": True, "server": info, "libraries": len(libs)})


@api.get("/libraries")
async def libraries():
    try:
        async with engine.jellyfin_client() as jf:
            libs = await jf.libraries()
    except JellyfinError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    counts = {r["library_id"]: r["n"] for r in db.query(
        "SELECT library_id, COUNT(*) AS n FROM items WHERE present = 1 GROUP BY library_id")}
    return _json({"libraries": [{
        "id": lib.id, "name": lib.name, "type": lib.collection_type or "mixed",
        **prefs.library_policy(lib.id, lib.name), "items": counts.get(lib.id, 0),
    } for lib in libs]})


@api.put("/libraries")
async def put_libraries(request: Request):
    body = await _body(request)
    libs = body.get("libraries")
    if not isinstance(libs, dict):
        raise HTTPException(status_code=400, detail="libraries must be an object")
    clean = {str(k): {"enabled": bool(v.get("enabled")),
                      "unmatched": "leave" if v.get("unmatched") == "leave" else "flag"}
             for k, v in libs.items() if isinstance(v, dict)}
    prefs.set("libraries", clean)
    return _json({"libraries": clean})


@api.post("/scan")
async def scan():
    if engine._run_lock.locked():
        raise HTTPException(status_code=409, detail="A run is in progress; it scans the library itself")
    try:
        async with engine.jellyfin_client() as jf:
            counts = await engine.scan(jf)
    except JellyfinError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _json(counts)


# ── Jellyfin thumbnails (the browser never sees the API key) ────────────────

@api.get("/thumb/{item_id}")
async def thumb(item_id: str, h: int = 360, tag: str = ""):
    """Jellyfin's current poster for an item.  With *tag* (its ImageTags.Primary)
    in the URL a new poster is a new URL, so the browser may keep this a week."""
    if not item_id.isalnum() or len(item_id) > 64:
        raise HTTPException(status_code=400, detail="Bad item id")
    try:
        data, ctype = await engine.shared_client().primary_image(item_id, max(90, min(h, 900)))
    except JellyfinError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(data, media_type=ctype,
                    headers={"Cache-Control": f"private, max-age={604800 if tag else 300}"})


router.include_router(api)
