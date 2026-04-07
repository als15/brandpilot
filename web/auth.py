"""Simple secret-based authentication for the dashboard."""

import hashlib
import hmac
import os
import time

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from web import templates

router = APIRouter()

_COOKIE_NAME = "capaco_session"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


def _sign(secret: str, value: str) -> str:
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode(), f"{ts}:{value}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"{ts}:{sig}"


def _verify(secret: str, token: str, max_age: int = _COOKIE_MAX_AGE) -> bool:
    try:
        ts, sig = token.split(":", 1)
        expected = hmac.new(secret.encode(), f"{ts}:ok".encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        if time.time() - int(ts) > max_age:
            return False
        return True
    except Exception:
        return False


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, secret: str):
        super().__init__(app)
        self.secret = secret

    async def dispatch(self, request: Request, call_next):
        # Allow static files, login page, and login POST without auth
        path = request.url.path
        if path.startswith("/static") or path in ("/login", "/health"):
            return await call_next(request)

        token = request.cookies.get(_COOKIE_NAME, "")
        if not _verify(self.secret, token):
            if "hx-request" in request.headers:
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login", status_code=302)

        return await call_next(request)


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request, "pages/login.html", {"error": ""})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    secret = os.environ.get("DASHBOARD_SECRET", "")
    password = form.get("password", "")

    if not secret or password != secret:
        return templates.TemplateResponse(
            request, "pages/login.html", {"error": "Invalid password"}, status_code=401
        )

    token = _sign(secret, "ok")
    response = RedirectResponse("/", status_code=302)
    response.set_cookie(_COOKIE_NAME, token, max_age=_COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(_COOKIE_NAME)
    return response


@router.get("/health")
async def health(request: Request):
    import asyncio
    from health import run_all_checks
    loop = asyncio.get_event_loop()
    scheduler = getattr(request.app.state, "scheduler", None)
    result = await loop.run_in_executor(None, run_all_checks, scheduler)
    status_code = 200 if result["healthy"] else 503
    from fastapi.responses import JSONResponse
    return JSONResponse(result, status_code=status_code)
