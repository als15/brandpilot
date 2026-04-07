"""Health checks for Capa & Co daemon. No LLM calls — purely deterministic."""

import logging
import os
from datetime import datetime, timezone, timedelta

import requests

from db.connection import get_db, _is_postgres

log = logging.getLogger("capaco")

ISR_TZ = timezone(timedelta(hours=3))


def check_db() -> tuple[bool, str]:
    """Test database connectivity."""
    try:
        db = get_db()
        db.execute("SELECT 1")
        return True, "connected"
    except Exception as e:
        return False, f"unreachable: {e}"


def check_instagram_token() -> tuple[bool, str]:
    """Verify Meta access token is valid."""
    token = os.environ.get("META_ACCESS_TOKEN", "")
    acct_id = os.environ.get("INSTAGRAM_ACCOUNT_ID", "")
    if not token or not acct_id:
        return False, "META_ACCESS_TOKEN or INSTAGRAM_ACCOUNT_ID not set"
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{acct_id}",
            params={"fields": "username"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if resp.status_code == 200:
            return True, f"valid (@{resp.json().get('username', '?')})"
        error = resp.json().get("error", {}).get("message", resp.text[:100])
        return False, f"invalid: {error}"
    except requests.Timeout:
        return False, "timeout reaching Meta API"
    except Exception as e:
        return False, f"error: {e}"


def check_scheduler(scheduler) -> tuple[bool, str]:
    """Verify APScheduler is running with jobs."""
    if scheduler is None:
        return False, "scheduler not available"
    try:
        if not scheduler.running:
            return False, "scheduler is stopped"
        jobs = scheduler.get_jobs()
        if not jobs:
            return False, "no scheduled jobs"
        # Find next run time
        next_times = [j.next_run_time for j in jobs if j.next_run_time]
        if next_times:
            soonest = min(next_times).strftime("%H:%M")
            return True, f"{len(jobs)} jobs, next at {soonest}"
        return True, f"{len(jobs)} jobs"
    except Exception as e:
        return False, f"error: {e}"


def check_recent_activity() -> tuple[bool, str]:
    """Check that tasks have been running in the last 24 hours."""
    try:
        db = get_db()
        if _is_postgres():
            recent_sql = "started_at >= NOW() - INTERVAL '24 hours'"
        else:
            recent_sql = "started_at >= datetime('now', '-24 hours')"

        total = db.execute(
            f"SELECT COUNT(*) as cnt FROM run_log WHERE {recent_sql} AND task_type != 'heartbeat'"
        ).fetchone()["cnt"]

        if total == 0:
            return False, "no task runs in last 24h"

        failed = db.execute(
            f"SELECT COUNT(*) as cnt FROM run_log WHERE {recent_sql} AND status = 'failed'"
        ).fetchone()["cnt"]

        rate = (failed / total * 100) if total > 0 else 0
        if rate > 50:
            return False, f"{failed}/{total} tasks failed ({rate:.0f}%)"
        return True, f"{total} runs, {failed} failed"
    except Exception as e:
        return False, f"error: {e}"


def check_overdue_posts() -> tuple[bool, str]:
    """Check for approved posts that should have been published >2 hours ago."""
    try:
        db = get_db()
        now_il = datetime.now(ISR_TZ)
        two_hours_ago = now_il - timedelta(hours=2)
        cutoff_date = two_hours_ago.strftime("%Y-%m-%d")
        cutoff_time = two_hours_ago.strftime("%H:%M")

        overdue = db.execute(
            "SELECT COUNT(*) as cnt FROM content_queue "
            "WHERE status = 'approved' AND image_url IS NOT NULL "
            "AND (scheduled_date < ? OR (scheduled_date = ? AND scheduled_time <= ?))",
            (cutoff_date, cutoff_date, cutoff_time),
        ).fetchone()["cnt"]

        if overdue > 0:
            return False, f"{overdue} approved post(s) overdue by >2h"
        return True, "no overdue posts"
    except Exception as e:
        return False, f"error: {e}"


def run_all_checks(scheduler=None) -> dict:
    """Run all health checks. Returns {"healthy": bool, "checks": {name: (ok, msg)}}."""
    checks = {
        "database": check_db(),
        "instagram_token": check_instagram_token(),
        "scheduler": check_scheduler(scheduler),
        "recent_activity": check_recent_activity(),
        "overdue_posts": check_overdue_posts(),
    }
    healthy = all(ok for ok, _ in checks.values())
    return {"healthy": healthy, "checks": checks}
