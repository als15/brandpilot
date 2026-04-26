"""Monitoring dashboard — FastAPI application factory."""

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

_WEB_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(_WEB_DIR / "templates"))


def _datefmt(value, fmt="%m-%d %H:%M"):
    """Jinja filter: format a datetime or string for display."""
    if value is None:
        return ""
    from datetime import datetime, date
    if isinstance(value, datetime):
        return value.strftime(fmt)
    if isinstance(value, date):
        return value.strftime(fmt)
    # Already a string — truncate to 16 chars like the old templates did
    return str(value)[:16]


templates.env.filters["datefmt"] = _datefmt


def create_app(scheduler=None, bot=None, safe_run_fn=None, brand_bots=None, brand_chat_ids=None):
    """Build the FastAPI app. Receives scheduler/bot from daemon for live access."""
    from brands.loader import brand_config, _list_brands, BrandConfig

    app = FastAPI(title=f"{brand_config.identity.name_en} Dashboard", docs_url=None, redoc_url=None)

    # Store shared objects so routes can access them
    app.state.scheduler = scheduler
    app.state.bot = bot
    app.state.safe_run = safe_run_fn
    app.state.brand_bots = brand_bots or {}
    app.state.brand_chat_ids = brand_chat_ids or {}

    # Store available brands for the brand selector
    app.state.available_brands = _list_brands()

    # Static files
    app.mount("/static", StaticFiles(directory=str(_WEB_DIR / "static")), name="static")

    # Auth middleware
    from web.auth import AuthMiddleware

    secret = os.environ.get("DASHBOARD_SECRET", "")
    if not secret:
        raise RuntimeError(
            "DASHBOARD_SECRET is not set. The dashboard refuses to start without "
            "auth — set DASHBOARD_SECRET (env var or .env) before launching."
        )
    app.add_middleware(AuthMiddleware, secret=secret)

    # Register routes
    from web.routes import dashboard, queue, logs, schedule, analytics, leads, engagement, system
    from web.auth import router as auth_router
    from web.brand_switcher import router as brand_router

    app.include_router(auth_router)
    app.include_router(brand_router)
    app.include_router(dashboard.router)
    app.include_router(queue.router)
    app.include_router(logs.router)
    app.include_router(schedule.router)
    app.include_router(analytics.router)
    app.include_router(leads.router)
    app.include_router(engagement.router)
    app.include_router(system.router)

    return app
