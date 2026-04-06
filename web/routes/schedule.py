"""Schedule page and task trigger endpoints."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from web import templates
from web.db import query

router = APIRouter()
log = logging.getLogger("capaco")

# Known schedule from daemon.py
SCHEDULE_DEFS = [
    {"task": "culinary_review", "label": "Culinary Review", "cron": "Sun 06:30"},
    {"task": "content_planning", "label": "Content Planning", "cron": "Sun 07:00"},
    {"task": "design_review", "label": "Design Review", "cron": "Sun 08:00"},
    {"task": "image_generation", "label": "Image Generation", "cron": "Sun 09:00"},
    {"task": "publish", "label": "Publish Posts", "cron": "Every 2h 06:00–20:00"},
    {"task": "publish_stories", "label": "Publish Stories", "cron": "Every 2h 06:00–20:00"},
    {"task": "analytics", "label": "Analytics", "cron": "Daily 18:00"},
    {"task": "content_review", "label": "Content Review", "cron": "Daily 19:00"},
    {"task": "lead_gen", "label": "Lead Generation", "cron": "Wed 10:00"},
    {"task": "engagement", "label": "Engagement Advisor", "cron": "Tue/Thu 10:00"},
]


@router.get("/schedule", response_class=HTMLResponse)
async def schedule_page(request: Request):
    from web.routes.dashboard import _global_stats

    # Last run per task type
    last_runs = await query(
        "SELECT task_type, "
        "MAX(CASE WHEN status='completed' THEN started_at END) as last_success, "
        "MAX(CASE WHEN status='failed' THEN started_at END) as last_failure "
        "FROM run_log GROUP BY task_type"
    )
    last_run_map = {r["task_type"]: r for r in last_runs}

    # Enrich schedule defs with run info
    schedule = []
    for defn in SCHEDULE_DEFS:
        entry = dict(defn)
        run_info = last_run_map.get(defn["task"], {})
        entry["last_success"] = run_info.get("last_success")
        entry["last_failure"] = run_info.get("last_failure")
        schedule.append(entry)

    # Upcoming content
    upcoming = await query(
        "SELECT id, scheduled_date, scheduled_time, content_type, topic, status, image_url "
        "FROM content_queue "
        "WHERE status IN ('draft', 'pending_approval', 'approved') "
        "AND scheduled_date >= date('now') "
        "ORDER BY scheduled_date, scheduled_time LIMIT 14"
    )

    # Next run times from scheduler if available
    next_runs = {}
    scheduler = request.app.state.scheduler
    if scheduler:
        for job in scheduler.get_jobs():
            next_run = job.next_run_time
            if next_run:
                next_runs[job.id] = next_run.strftime("%Y-%m-%d %H:%M")

    stats = await _global_stats()

    return templates.TemplateResponse(request, "pages/schedule.html", {
        "active_page": "schedule",
        "stats": stats,
        "schedule": schedule,
        "upcoming": upcoming,
        "next_runs": next_runs,
    })


@router.post("/schedule/{task_type}/run", response_class=HTMLResponse)
async def trigger_task(request: Request, task_type: str):
    safe_run = request.app.state.safe_run
    bot = request.app.state.bot

    if not safe_run:
        return HTMLResponse('<span class="badge badge-failed">No runner available</span>')

    valid_tasks = {d["task"] for d in SCHEDULE_DEFS}
    if task_type not in valid_tasks:
        return HTMLResponse(f'<span class="badge badge-failed">Unknown task: {task_type}</span>')

    log.info(f"Dashboard triggered task: {task_type}")
    asyncio.create_task(safe_run(task_type, bot))

    return HTMLResponse(
        f'<button class="btn btn-secondary btn-sm" disabled>Triggered: {task_type}</button>'
    )
