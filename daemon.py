"""Autonomous scheduler daemon for Instagram management.
Runs the Telegram bot and scheduled agent tasks in a single async event loop."""

from dotenv import load_dotenv

load_dotenv()

import asyncio
import logging
import os
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import brands.loader
from brands.loader import init_brand, set_brand, load_all_brands, _list_brands
from db.schema import init_db
from db.connection import get_db
from graph.orchestrator import run_task
from tools.token_refresh import (
    load_persisted_token,
    refresh_meta_token,
    token_expires_in_days,
)
from telegram_bot import (
    build_telegram_app,
    notify_task_complete,
    notify_error,
    notify_pending_approval,
    notify_publish_success,
    notify_publish_failure,
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


# Tasks that should skip silently when there's nothing to publish
SKIP_WHEN_EMPTY = {"publish", "publish_stories"}

# Sunday pipeline dependencies: task -> prerequisite that must have succeeded today
TASK_DEPENDENCIES = {
    "design_review": "content_planning",
    "image_generation": "design_review",
}

# Per-task timeout in seconds
TASK_TIMEOUTS = {
    "publish": 300,
    "publish_stories": 300,
    "image_generation": 600,
    "content_planning": 600,
    "content_review": 600,
}
DEFAULT_TIMEOUT = 300


def _dependency_met(task_type: str) -> tuple[bool, str]:
    """Check if the prerequisite task for this task succeeded today. Returns (ok, reason)."""
    dep = TASK_DEPENDENCIES.get(task_type)
    if not dep:
        return True, ""

    from zoneinfo import ZoneInfo
    from db.connection import _is_postgres
    today = datetime.now(ZoneInfo(brands.loader.brand_config.identity.timezone)).strftime("%Y-%m-%d")

    db = get_db()
    if _is_postgres():
        row = db.execute(
            "SELECT status FROM run_log WHERE task_type = %s AND brand_id = %s "
            "AND started_at::date = %s::date ORDER BY started_at DESC LIMIT 1",
            (dep, brands.loader.brand_config.slug, today),
        ).fetchone()
    else:
        row = db.execute(
            "SELECT status FROM run_log WHERE task_type = ? AND brand_id = ? "
            "AND date(started_at) = ? ORDER BY started_at DESC LIMIT 1",
            (dep, brands.loader.brand_config.slug, today),
        ).fetchone()

    if not row:
        return False, f"Prerequisite '{dep}' has not run today"
    if row["status"] != "completed":
        return False, f"Prerequisite '{dep}' {row['status']} today — skipping {task_type}"
    return True, ""


def _has_publishable_content(task_type: str) -> bool:
    """Check if there are approved posts ready to publish for the given task type."""
    from zoneinfo import ZoneInfo
    now_il = datetime.now(ZoneInfo(brands.loader.brand_config.identity.timezone))
    today = now_il.strftime("%Y-%m-%d")
    now_time = now_il.strftime("%H:%M")

    db = get_db()
    content_type = "photo" if task_type == "publish" else "story"
    row = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue "
        "WHERE status = 'approved' AND content_type = ? AND brand_id = ? AND image_url IS NOT NULL "
        "AND (scheduled_date < ? OR (scheduled_date = ? AND scheduled_time <= ?))",
        (content_type, brands.loader.brand_config.slug, today, today, now_time),
    ).fetchone()
    return row["cnt"] > 0


def _skip_reason(content_type: str) -> str:
    """Diagnose why there's nothing to publish — returns a human-readable reason."""
    from zoneinfo import ZoneInfo
    bid = brands.loader.brand_config.slug
    now_il = datetime.now(ZoneInfo(brands.loader.brand_config.identity.timezone))
    today = now_il.strftime("%Y-%m-%d")
    now_time = now_il.strftime("%H:%M")

    db = get_db()
    approved = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue "
        "WHERE status = 'approved' AND content_type = ? AND brand_id = ?",
        (content_type, bid),
    ).fetchone()["cnt"]
    if approved == 0:
        statuses = db.execute(
            "SELECT status, COUNT(*) as cnt FROM content_queue "
            "WHERE content_type = ? AND brand_id = ? AND scheduled_date = ? GROUP BY status",
            (content_type, bid, today),
        ).fetchall()
        if not statuses:
            return f"No {content_type} posts scheduled for today ({today})."
        breakdown = ", ".join(f"{r['cnt']} {r['status']}" for r in statuses)
        return f"No approved {content_type} posts. Today's posts: {breakdown}."

    future = db.execute(
        "SELECT scheduled_date, scheduled_time FROM content_queue "
        "WHERE status = 'approved' AND content_type = ? AND brand_id = ? AND image_url IS NOT NULL "
        "AND scheduled_date = ? AND scheduled_time > ? "
        "ORDER BY scheduled_time LIMIT 3",
        (content_type, bid, today, now_time),
    ).fetchall()
    if future:
        times = ", ".join(r["scheduled_time"] for r in future)
        return f"Approved {content_type} posts exist but scheduled later today: {times}. Now is {now_time}."

    no_img = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue "
        "WHERE status = 'approved' AND content_type = ? AND brand_id = ? AND image_url IS NULL",
        (content_type, bid),
    ).fetchone()["cnt"]
    if no_img:
        return f"{no_img} approved {content_type} post(s) but missing image_url."

    return f"No publishable {content_type} posts found (approved: {approved}, now: {today} {now_time})."


async def safe_run(task_type: str, bot, brand_slug: str | None = None):
    """Run a task in a thread executor (LangGraph/Ollama are sync) and notify via Telegram.

    If brand_slug is provided, switches global brand_config to that brand before execution.
    """
    if brand_slug:
        set_brand(brand_slug)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    loop = asyncio.get_event_loop()

    # Reset retryable failed posts back to approved before publishing
    if task_type in SKIP_WHEN_EMPTY:
        content_type = "photo" if task_type == "publish" else "story"
        db = get_db()
        db.execute(
            "UPDATE content_queue SET status = 'approved' "
            "WHERE status = 'failed' AND content_type = ? AND brand_id = ? AND retry_count < 3",
            (content_type, brands.loader.brand_config.slug),
        )
        db.commit()

    # Skip publish tasks if nothing to publish (but still log it)
    if task_type in SKIP_WHEN_EMPTY and not _has_publishable_content(task_type):
        log.info(f"Skipping {task_type}: nothing to publish.")
        db = get_db()
        content_type = "photo" if task_type == "publish" else "story"
        reason = _skip_reason(content_type)
        db.execute(
            "INSERT INTO run_log (brand_id, task_type, status, duration_seconds, summary) VALUES (?, ?, ?, ?, ?)",
            (brands.loader.brand_config.slug, task_type, "skipped", 0, reason),
        )
        db.commit()
        return

    # Skip if a prerequisite task failed or hasn't run today
    dep_ok, dep_reason = _dependency_met(task_type)
    if not dep_ok:
        log.warning(f"Skipping {task_type}: {dep_reason}")
        db = get_db()
        db.execute(
            "INSERT INTO run_log (brand_id, task_type, status, duration_seconds, summary) VALUES (?, ?, ?, ?, ?)",
            (brands.loader.brand_config.slug, task_type, "skipped", 0, dep_reason),
        )
        db.commit()
        if chat_id:
            await notify_error(bot, task_type, f"SKIPPED: {dep_reason}")
        return

    log.info(f"Starting scheduled task: {task_type}")
    timeout = TASK_TIMEOUTS.get(task_type, DEFAULT_TIMEOUT)
    try:
        summary = await asyncio.wait_for(
            loop.run_in_executor(None, run_task, task_type),
            timeout=timeout,
        )
        log.info(f"Completed {task_type}: {summary[:200]}")

        # Publish tasks: send per-post notifications
        if task_type in ("publish", "publish_stories") and chat_id:
            await _notify_publish_results(bot, task_type)
        elif chat_id:
            await notify_task_complete(bot, task_type, summary)

        # After image generation, send approval notifications for new pending posts
        if task_type == "image_generation" and chat_id:
            await _send_pending_approvals(bot)

        # After content review, run image gen for any revised posts reset to draft
        if task_type == "content_review" and chat_id:
            db = get_db()
            drafts = db.execute(
                "SELECT COUNT(*) as cnt FROM content_queue "
                "WHERE status = 'draft' AND brand_id = ? AND visual_direction IS NOT NULL AND image_url IS NULL",
                (brands.loader.brand_config.slug,),
            ).fetchone()
            if drafts["cnt"] > 0:
                log.info(f"Content review revised {drafts['cnt']} posts, triggering image generation...")
                await safe_run("image_generation", bot)

    except asyncio.TimeoutError:
        log.error(f"Task {task_type} timed out after {timeout}s")
        db = get_db()
        db.execute(
            "INSERT INTO run_log (brand_id, task_type, status, duration_seconds, error) VALUES (?, ?, ?, ?, ?)",
            (brands.loader.brand_config.slug, task_type, "timeout", timeout, f"Task exceeded {timeout}s timeout"),
        )
        db.commit()
        if chat_id:
            await notify_error(bot, task_type, f"Task timed out after {timeout}s")
    except Exception as e:
        log.error(f"Failed {task_type}: {e}")
        try:
            db = get_db()
            error_cat = "unknown"
            err_str = str(e).lower()
            if "401" in err_str or "unauthorized" in err_str or "token" in err_str:
                error_cat = "auth_error"
            elif "timeout" in err_str or "timed out" in err_str:
                error_cat = "timeout"
            elif "database" in err_str or "connection" in err_str:
                error_cat = "db_error"
            db.execute(
                "INSERT INTO run_log (brand_id, task_type, status, duration_seconds, error, error_category) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (brands.loader.brand_config.slug, task_type, "failed", 0, str(e)[:500], error_cat),
            )
            db.commit()
        except Exception:
            log.error(f"Failed to log error for {task_type} to run_log")
        if chat_id:
            await notify_error(bot, task_type, str(e))


async def _send_pending_approvals(bot):
    """Send Telegram notifications for posts awaiting approval."""
    db = get_db()
    rows = db.execute(
        "SELECT id, topic, caption, image_url FROM content_queue "
        "WHERE status = 'pending_approval' AND brand_id = ? AND image_url IS NOT NULL",
        (brands.loader.brand_config.slug,),
    ).fetchall()

    for row in rows:
        await notify_pending_approval(
            bot,
            post_id=row["id"],
            topic=row["topic"] or "",
            caption=row["caption"] or "",
            image_url=row["image_url"],
        )


async def _notify_publish_results(bot, task_type: str):
    """Send per-post Telegram notifications for publish results."""
    from db.connection import _is_postgres
    db = get_db()
    content_type = "photo" if task_type == "publish" else "story"

    # Posts published in last 5 minutes
    if _is_postgres():
        recent_sql = "published_at >= NOW() - INTERVAL '5 minutes'"
    else:
        recent_sql = "published_at >= datetime('now', '-5 minutes')"

    published = db.execute(
        f"SELECT id, topic, image_url FROM content_queue "
        f"WHERE status = 'published' AND content_type = ? AND {recent_sql}",
        (content_type,),
    ).fetchall()
    for post in published:
        try:
            await notify_publish_success(
                bot, post["id"], post["topic"] or "", post["image_url"] or "",
            )
        except Exception as e:
            log.warning(f"Failed to send publish notification for post {post['id']}: {e}")

    # Posts that are failed
    failed = db.execute(
        "SELECT id, topic FROM content_queue "
        "WHERE status = 'failed' AND content_type = ?",
        (content_type,),
    ).fetchall()
    for post in failed:
        try:
            await notify_publish_failure(
                bot, post["id"], post["topic"] or "",
            )
        except Exception as e:
            log.warning(f"Failed to send failure notification for post {post['id']}: {e}")


async def _health_check_job(bot, scheduler):
    """Periodic health check — alerts on Telegram only when something is wrong."""
    from health import run_all_checks
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    loop = asyncio.get_event_loop()
    try:
        result = await loop.run_in_executor(None, run_all_checks, scheduler)

        # Write heartbeat to run_log
        db = get_db()
        db.execute(
            "INSERT INTO run_log (task_type, status, duration_seconds, summary) VALUES (?, ?, ?, ?)",
            ("heartbeat", "ok", 0, "Health check passed" if result["healthy"] else "Issues detected"),
        )
        db.commit()

        if not result["healthy"] and chat_id:
            failures = [f"- {k}: {v[1]}" for k, v in result["checks"].items() if not v[0]]
            text = "HEALTH CHECK ALERT\n\n" + "\n".join(failures)
            await bot.send_message(chat_id=chat_id, text=text)

        # Dead man's switch: ping external monitor so it knows we're alive.
        # If pings stop, the external service alerts via Telegram.
        ping_url = os.environ.get("HEALTHCHECK_PING_URL")
        if ping_url:
            import httpx
            async with httpx.AsyncClient() as client:
                await client.get(ping_url, timeout=10)
    except Exception as e:
        log.error(f"Health check failed: {e}")


async def safe_refresh_token(bot, brand_slug):
    """Refresh Meta token for a specific brand with error notification.

    Must take the brand slug explicitly — the scheduled job fires in a bare
    thread context with no guarantee about which brand the global
    ``brand_config`` singleton is pointing at, so we reset it here before
    reading credentials or writing to the DB.
    """
    set_brand(brand_slug)
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    try:
        refresh_meta_token(brand_slug)
        log.info(f"Meta token refreshed successfully for {brand_slug}.")
        if chat_id:
            await bot.send_message(
                chat_id=chat_id,
                text=f"Meta API token refreshed successfully for {brand_slug}.",
            )
    except Exception as e:
        log.error(f"Token refresh failed for {brand_slug}: {e}")
        if chat_id:
            await bot.send_message(
                chat_id=chat_id,
                text=f"TOKEN REFRESH FAILED ({brand_slug}): {e}",
            )


async def check_token_expiry(bot, chat_id, brand_slug):
    """Warn via Telegram if the stored token is expired or close to expiry.

    Runs at startup and then daily. Reads expiry from brand_credentials, so it
    only fires once a refresh has written to the DB — on a fresh deploy we stay
    silent until the first refresh (we have no reliable source of truth for
    bootstrap-env-var expiry).
    """
    days = token_expires_in_days(brand_slug)
    if days is None:
        log.info(f"No persisted token expiry for {brand_slug} yet; skipping check.")
        return

    if days < 0:
        msg = (
            f"TOKEN EXPIRED for {brand_slug} {-days} day(s) ago. "
            f"Rotate the brand-prefixed META_ACCESS_TOKEN in Railway and restart."
        )
        log.error(msg)
    elif days <= 7:
        msg = (
            f"Meta token for {brand_slug} expires in {days} day(s). "
            f"Scheduled auto-refresh should run soon; verify it does."
        )
        log.warning(msg)
    else:
        log.info(f"Meta token for {brand_slug} expires in {days} day(s); OK.")
        return

    if bot and chat_id:
        try:
            await bot.send_message(chat_id=chat_id, text=msg)
        except Exception as e:
            log.warning(f"Failed to send token expiry alert for {brand_slug}: {e}")


def _register_brand_jobs(scheduler, bot, bc):
    """Register all scheduled jobs for a single brand."""
    sched = bc.schedule
    slug = bc.slug

    # ── Weekly Planning Session ──
    scheduler.add_job(safe_run, "cron", day_of_week=sched.planning_day,
                      hour=sched.culinary_review_hour, minute=sched.culinary_review_minute,
                      args=["culinary_review", bot, slug], id=f"culinary_review_{slug}",
                      timezone=bc.identity.timezone)
    scheduler.add_job(safe_run, "cron", day_of_week=sched.planning_day,
                      hour=sched.planning_hour,
                      args=["content_planning", bot, slug], id=f"content_planning_{slug}",
                      timezone=bc.identity.timezone)
    scheduler.add_job(safe_run, "cron", day_of_week=sched.planning_day,
                      hour=sched.design_review_hour,
                      args=["design_review", bot, slug], id=f"design_review_{slug}",
                      timezone=bc.identity.timezone)
    scheduler.add_job(safe_run, "cron", day_of_week=sched.planning_day,
                      hour=sched.image_generation_hour,
                      args=["image_generation", bot, slug], id=f"image_generation_{slug}",
                      timezone=bc.identity.timezone)

    # ── Daily Publishing ──
    scheduler.add_job(safe_run, "cron", hour=sched.publish_hours,
                      args=["publish", bot, slug], id=f"publish_{slug}",
                      timezone=bc.identity.timezone)
    scheduler.add_job(safe_run, "cron", hour=sched.publish_hours,
                      args=["publish_stories", bot, slug], id=f"publish_stories_{slug}",
                      timezone=bc.identity.timezone)

    # ── Daily Performance Review ──
    scheduler.add_job(safe_run, "cron", hour=sched.analytics_hour,
                      args=["analytics", bot, slug], id=f"analytics_{slug}",
                      timezone=bc.identity.timezone)
    scheduler.add_job(safe_run, "cron", hour=sched.content_review_hour,
                      args=["content_review", bot, slug], id=f"content_review_{slug}",
                      timezone=bc.identity.timezone)

    # Lead generation
    scheduler.add_job(safe_run, "cron", day_of_week=sched.lead_gen_day,
                      hour=sched.lead_gen_hour,
                      args=["lead_gen", bot, slug], id=f"lead_gen_{slug}",
                      timezone=bc.identity.timezone)

    # Engagement
    scheduler.add_job(safe_run, "cron", day_of_week=sched.engagement_days,
                      hour=sched.engagement_hour,
                      args=["engagement", bot, slug], id=f"engagement_{slug}",
                      timezone=bc.identity.timezone)

    # Token refresh
    scheduler.add_job(safe_refresh_token, "interval", days=sched.token_refresh_days,
                      args=[bot, slug], id=f"token_refresh_{slug}")

    # Daily token expiry check — proactive alert, independent of refresh cadence
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    scheduler.add_job(check_token_expiry, "interval", hours=24,
                      args=[bot, chat_id, slug], id=f"token_expiry_{slug}")


async def main():
    # In multi-brand mode, init_brand() needs an explicit slug since
    # _resolve_slug() raises when multiple brands exist without BRAND env var.
    available = _list_brands()
    bc = init_brand(available[0] if available else None)
    os.makedirs("data", exist_ok=True)
    init_db()

    all_brands = load_all_brands()

    log.info("=" * 50)
    log.info("Daemon Starting")
    log.info(f"Brands: {', '.join(b.slug for b in all_brands)}")
    log.info(f"Time: {datetime.now().isoformat()}")
    log.info(f"LLM Provider: {os.environ.get('LLM_PROVIDER', 'ollama')}")
    log.info(f"Database: {'Postgres' if os.environ.get('DATABASE_URL', '').startswith('postgres') else 'SQLite'}")
    log.info("=" * 50)

    # ── Build per-brand Telegram bots ──
    # Each brand may have its own bot token + chat_id in its .env.
    # We deduplicate by token so brands sharing a token share one Application.
    brand_bots = {}      # slug -> Bot
    brand_chat_ids = {}  # slug -> chat_id
    telegram_apps = []   # deduplicated Application list
    _tokens_seen = {}    # token -> Application

    for brand in all_brands:
        set_brand(brand.slug)  # loads brand .env (sets TELEGRAM_BOT_TOKEN, CHAT_ID)
        # Hydrate META_ACCESS_TOKEN from brand_credentials if a persisted
        # token exists — this is what survives Railway redeploys. Falls
        # through to the brand-prefixed env var on bootstrap (no DB row yet).
        load_persisted_token(brand.slug)
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if not token:
            log.warning(f"No TELEGRAM_BOT_TOKEN for {brand.slug}, skipping Telegram")
            continue
        brand_chat_ids[brand.slug] = chat_id
        if token not in _tokens_seen:
            app = build_telegram_app(token)
            app.bot_data["brand_slug"] = brand.slug
            _tokens_seen[token] = app
            telegram_apps.append(app)
        else:
            # Two brands sharing a token collapse to the first one's context.
            log.warning(
                f"{brand.slug} shares a Telegram token with "
                f"{_tokens_seen[token].bot_data.get('brand_slug')} — "
                "handler responses will reflect only the first brand."
            )
        brand_bots[brand.slug] = _tokens_seen[token].bot
        log.info(f"Telegram for {brand.slug}: chat_id={chat_id[:6]}... bot={token[:10]}...")

    # Restore first brand as default
    set_brand(available[0])

    # ── Scheduler ──
    scheduler = AsyncIOScheduler(timezone="UTC")

    for app in telegram_apps:
        app.bot_data["scheduler"] = scheduler

    # Register jobs for each brand with its specific bot.
    # set_brand() before registering so env-derived values (chat_id) are
    # pinned at registration time rather than whatever brand was active last.
    for brand in all_brands:
        bot = brand_bots.get(brand.slug)
        if not bot:
            log.warning(f"No Telegram bot for {brand.slug}, jobs will run without notifications")
            continue
        set_brand(brand.slug)
        log.info(f"Registering jobs for brand: {brand.slug} (tz={brand.identity.timezone})")
        _register_brand_jobs(scheduler, bot, brand)

    # Health check uses the first available bot
    primary_bot = next(iter(brand_bots.values()), None)
    if primary_bot:
        scheduler.add_job(_health_check_job, "interval", minutes=30,
                          args=[primary_bot, scheduler], id="health_check")

    scheduler.start()

    log.info("Scheduled jobs:")
    for job in scheduler.get_jobs():
        log.info(f"  {job.id}: {job.trigger}")

    # ── Web dashboard ──
    import uvicorn
    from web import create_app

    web_app = create_app(scheduler=scheduler, bot=primary_bot, safe_run_fn=safe_run)
    port = int(os.environ.get("PORT", 8000))
    uvi_config = uvicorn.Config(web_app, host="0.0.0.0", port=port, log_level="info")
    uvi_server = uvicorn.Server(uvi_config)

    async def _run_web_server():
        """Run web server — failure won't crash the daemon."""
        try:
            await uvi_server.serve()
        except SystemExit:
            log.error(f"Web server failed to start on port {port} (port in use?). "
                      "Scheduler and Telegram bot will continue without the dashboard.")
        except Exception as e:
            log.error(f"Web server crashed: {e}. "
                      "Scheduler and Telegram bot will continue without the dashboard.")

    log.info(f"Starting web dashboard on port {port}...")
    log.info(f"Starting Telegram polling for {len(telegram_apps)} bot(s)...")

    # ── Start all Telegram apps ──
    from contextlib import AsyncExitStack

    async with AsyncExitStack() as stack:
        for app in telegram_apps:
            await stack.enter_async_context(app)
            await app.start()
            await app.updater.start_polling()

        # Send per-brand online notification + proactive token expiry check.
        for brand in all_brands:
            bot = brand_bots.get(brand.slug)
            chat_id = brand_chat_ids.get(brand.slug, "")
            if bot and chat_id:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=f"Bot is online! Brand: {brand.identity.name_en}",
                    )
                except Exception as e:
                    log.warning(f"Failed to send online message for {brand.slug}: {e}")
            try:
                await check_token_expiry(bot, chat_id, brand.slug)
            except Exception as e:
                log.warning(f"Startup token expiry check failed for {brand.slug}: {e}")

        web_task = asyncio.create_task(_run_web_server())

        log.info("Daemon running. Press Ctrl+C to stop.")

        stop_event = asyncio.Event()
        try:
            await stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            uvi_server.should_exit = True
            await web_task
            for app in telegram_apps:
                await app.updater.stop()
                await app.stop()
            scheduler.shutdown()
            log.info("Daemon stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Daemon stopped by user.")
    except Exception as e:
        import traceback
        traceback.print_exc()
        log.error(f"Daemon crashed: {e}")
