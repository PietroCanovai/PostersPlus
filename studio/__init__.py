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


def install(app) -> None:
    app.include_router(router)
    app.mount("/studio/static", StaticFiles(directory=WEB_DIR), name="studio-static")


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
