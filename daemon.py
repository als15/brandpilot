"""Autonomous scheduler daemon for Capa & Co Instagram management.
Runs the Telegram bot and scheduled agent tasks in a single async event loop."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from db.schema import init_db
from db.connection import get_db
from graph.orchestrator import run_task
from tools.token_refresh import load_persisted_token, refresh_meta_token
from telegram_bot import (
    build_telegram_app,
    notify_task_complete,
    notify_error,
    notify_pending_approval,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("data/daemon.log"),
    ],
)
log = logging.getLogger("capaco")


async def safe_run(task_type: str, bot):
    """Run a task in a thread executor (LangGraph/Ollama are sync) and notify via Telegram."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    loop = asyncio.get_event_loop()

    log.info(f"Starting scheduled task: {task_type}")
    try:
        summary = await loop.run_in_executor(None, run_task, task_type)
        log.info(f"Completed {task_type}: {summary[:200]}")

        if chat_id:
            await notify_task_complete(bot, task_type, summary)

        # After image generation, send approval notifications for new pending posts
        if task_type == "image_generation" and chat_id:
            await _send_pending_approvals(bot)

    except Exception as e:
        log.error(f"Failed {task_type}: {e}")
        if chat_id:
            await notify_error(bot, task_type, str(e))


async def _send_pending_approvals(bot):
    """Send Telegram notifications for posts awaiting approval."""
    db = get_db()
    rows = db.execute(
        "SELECT id, topic, caption, image_url FROM content_queue "
        "WHERE status = 'pending_approval' AND image_url IS NOT NULL"
    ).fetchall()

    for row in rows:
        await notify_pending_approval(
            bot,
            post_id=row["id"],
            topic=row["topic"] or "",
            caption=row["caption"] or "",
            image_url=row["image_url"],
        )


async def safe_refresh_token(bot):
    """Refresh Meta token with error notification."""
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        refresh_meta_token()
        log.info("Meta token refreshed successfully.")
        if chat_id:
            await bot.send_message(chat_id=chat_id, text="Meta API token refreshed successfully.")
    except Exception as e:
        log.error(f"Token refresh failed: {e}")
        if chat_id:
            await bot.send_message(chat_id=chat_id, text=f"TOKEN REFRESH FAILED: {e}")


async def main():
    os.makedirs("data", exist_ok=True)
    init_db()
    load_persisted_token()

    log.info("=" * 50)
    log.info("Capa & Co Daemon Starting")
    log.info(f"Time: {datetime.now().isoformat()}")
    log.info(f"LLM Provider: {os.environ.get('LLM_PROVIDER', 'ollama')}")
    log.info("=" * 50)

    # Build Telegram app
    telegram_app = build_telegram_app()
    bot = telegram_app.bot

    # Set up scheduler
    scheduler = AsyncIOScheduler()

    # Content planning: Monday 07:00
    scheduler.add_job(safe_run, "cron", day_of_week="mon", hour=7,
                      args=["content_planning", bot], id="content_planning")

    # Design review: Monday 08:00 (after content planning)
    scheduler.add_job(safe_run, "cron", day_of_week="mon", hour=8,
                      args=["design_review", bot], id="design_review")

    # Image generation: every 6 hours (processes drafts)
    scheduler.add_job(safe_run, "cron", hour="*/6",
                      args=["image_generation", bot], id="image_generation")

    # Publish approved posts: weekdays at noon
    scheduler.add_job(safe_run, "cron", day_of_week="mon-fri", hour=12,
                      args=["publish", bot], id="publish")

    # Analytics: daily at 18:00
    scheduler.add_job(safe_run, "cron", hour=18,
                      args=["analytics", bot], id="analytics")

    # Lead generation: Wednesday 10:00
    scheduler.add_job(safe_run, "cron", day_of_week="wed", hour=10,
                      args=["lead_gen", bot], id="lead_gen")

    # Engagement: Tuesday and Thursday 10:00
    scheduler.add_job(safe_run, "cron", day_of_week="tue,thu", hour=10,
                      args=["engagement", bot], id="engagement")

    # Token refresh: every 50 days
    scheduler.add_job(safe_refresh_token, "interval", days=50,
                      args=[bot], id="token_refresh")

    scheduler.start()

    log.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        log.info(f"  {job.id}: {job.trigger}")

    log.info("Starting Telegram bot polling...")

    # Start Telegram polling alongside the scheduler
    async with telegram_app:
        await telegram_app.start()
        await telegram_app.updater.start_polling()

        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if chat_id:
            await bot.send_message(chat_id=chat_id, text="Capa & Co bot is online!")

        log.info("Daemon running. Press Ctrl+C to stop.")

        # Keep running until interrupted
        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            scheduler.shutdown()
            log.info("Daemon stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Daemon stopped by user.")
