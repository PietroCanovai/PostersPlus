"""PostersPlus Studio: the Jellyfin library manager inside PostersPlus.

main.py calls install(app) once and start()/stop() from its lifespan; nothing
else in upstream code knows about Studio.  See PLAN.md and CLAUDE.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import suppress

from fastapi.staticfiles import StaticFiles

from . import db, engine
from .api import WEB_DIR, router

logger = logging.getLogger("studio")

_task: asyncio.Task | None = None
_lock_fh = None


class _RevalidatedStatic(StaticFiles):
    """Studio's page files, always revalidated (a cheap 304) so an update is
    picked up at the next load instead of whenever the browser's cache decides."""

    def file_response(self, *args, **kwargs):
        resp = super().file_response(*args, **kwargs)
        resp.headers["Cache-Control"] = "no-cache"
        return resp


def install(app) -> None:
    from .api_library import router as library_router
    from .api_notch import router as notch_router
    from .api_style import router as style_router
    app.include_router(router)
    app.include_router(library_router)
    app.include_router(style_router)
    app.include_router(notch_router)
    app.mount("/studio/static", _RevalidatedStatic(directory=WEB_DIR), name="studio-static")


def _take_lock() -> bool:
    """One scheduler per instance, even with several uvicorn workers."""
    global _lock_fh
    import fcntl  # POSIX only; imported here so the package loads anywhere for tests
    path = os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), ".studio.lock")
    try:
        fh = open(path, "a")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, BlockingIOError):
        return False
    _lock_fh = fh
    return True


def start() -> None:
    global _task
    db.connect()
    if _take_lock():
        _task = asyncio.create_task(engine.scheduler_loop())
        logger.info("Studio ready at /studio (scheduler in this worker)")
    else:
        logger.info("Studio ready at /studio (another worker runs the scheduler)")


async def stop() -> None:
    if _task is not None:
        _task.cancel()
        with suppress(asyncio.CancelledError):
            await _task
