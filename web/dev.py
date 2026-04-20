"""Dev server entry point — run with: uvicorn web.dev:app --reload"""

import asyncio
import logging
import os
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

# Use production Postgres if DATABASE_URL is set in .env, otherwise fall back to SQLite

from db.schema import init_db

init_db()

log = logging.getLogger("capaco")


async def _dev_safe_run(task_type: str, bot=None, brand_slug: str = "capa-co"):
    """Standalone runner for local dev — no daemon or Telegram needed."""
    from graph.orchestrator import run_task

    loop = asyncio.get_event_loop()
    log.info(f"[dev] Starting task: {task_type} for {brand_slug}")
    try:
        summary = await loop.run_in_executor(None, run_task, task_type, brand_slug)
        log.info(f"[dev] Completed {task_type}: {summary[:200]}")
    except Exception as e:
        log.error(f"[dev] Failed {task_type}: {e}")


from web import create_app

app = create_app(safe_run_fn=_dev_safe_run)
