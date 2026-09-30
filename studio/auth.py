"""Studio login: the instance's ADMIN_KEY once, then a signed session cookie.

The key check goes through admin._authorise, so Studio shares the dashboard's
wrong-key delay and lockout.  The cookie is HttpOnly and SameSite=Strict, and
every state-changing call must also carry the X-Studio header, which a
cross-site form or image can't send.
"""
from __future__ import annotations

import hashlib
import hmac
import time

from fastapi import HTTPException, Request, Response

from . import prefs

COOKIE = "pp_studio"
SESSION_DAYS = 30
_SAFE_METHODS = ("GET", "HEAD", "OPTIONS")


def _sign(expires: int) -> str:
    mac = hmac.new(prefs.session_secret().encode(), f"studio:{expires}".encode(), hashlib.sha256)
    return f"{expires}.{mac.hexdigest()}"


def valid_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, _, _mac = token.partition(".")
    if not expires.isdigit() or int(expires) < (now or time.time()):
        return False
    return hmac.compare_digest(token.encode(), _sign(int(expires)).encode())


def _https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


def set_session(request: Request, response: Response) -> None:
    expires = int(time.time()) + SESSION_DAYS * 86400
    response.set_cookie(COOKIE, _sign(expires), max_age=SESSION_DAYS * 86400, httponly=True,
                        samesite="strict", secure=_https(request), path="/studio")


def clear_session(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/studio")


async def login(request: Request, key: str) -> None:
    import admin
    await admin._authorise(request, key or "")


async def require(request: Request) -> None:
    """FastAPI dependency for every /studio/api route except login."""
    if request.method not in _SAFE_METHODS and request.headers.get("x-studio") != "1":
        raise HTTPException(status_code=403, detail="Missing X-Studio header")
    if valid_token(request.cookies.get(COOKIE)):
        return
    key = request.headers.get("x-admin-key")
    if key:
        await login(request, key)
        return
    raise HTTPException(status_code=401, detail="Log in first")
