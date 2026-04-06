"""Content queue pages and actions."""

import asyncio
import json
import logging
from collections import OrderedDict
from datetime import date, timedelta
from functools import partial

from fastapi import APIRouter, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse

from web import templates
from web.db import query, query_one, execute

router = APIRouter()
log = logging.getLogger("capaco")

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    from web.routes.dashboard import _global_stats

    view = request.query_params.get("view", "grid")
    status_filter = request.query_params.get("status", "")
    type_filter = request.query_params.get("type", "")

    # Build query
    conditions = []
    params = []
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("content_type = ?")
        params.append(type_filter)

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    rows = await query(
        f"SELECT id, scheduled_date, scheduled_time, content_type, content_pillar, "
        f"topic, status, image_url FROM content_queue {where} "
        f"ORDER BY scheduled_date ASC, scheduled_time ASC LIMIT 200",
        tuple(params),
    )

    # Status counts for filter pills
    counts_rows = await query(
        "SELECT status, COUNT(*) as count FROM content_queue GROUP BY status"
    )
    counts = {r["status"]: r["count"] for r in counts_rows}

    stats = await _global_stats()

    # For timeline view, group posts by date into weeks
    timeline_weeks = []
    if view == "timeline":
        # Determine the week range: show current week + next 2 weeks
        today = date.today()
        week_start = today - timedelta(days=today.weekday())  # Monday

        for w in range(3):
            wk_start = week_start + timedelta(weeks=w)
            days = OrderedDict()
            for d in range(7):
                day = wk_start + timedelta(days=d)
                day_str = day.isoformat()
                days[day_str] = {
                    "date": day_str,
                    "day_name": DAY_NAMES[d],
                    "day_num": day.day,
                    "is_today": day == today,
                    "posts": [],
                }
            # Fill in posts
            for post in rows:
                sd = post.get("scheduled_date", "")
                if isinstance(sd, date) and not isinstance(sd, str):
                    sd = sd.isoformat()
                sd = str(sd)[:10] if sd else ""
                if sd in days:
                    days[sd]["posts"].append(post)

            timeline_weeks.append({
                "label": f"{wk_start.strftime('%b %d')} — {(wk_start + timedelta(days=6)).strftime('%b %d')}",
                "days": list(days.values()),
            })

    template = "pages/queue_timeline.html" if view == "timeline" else "pages/queue.html"

    return templates.TemplateResponse(request, template, {
        "active_page": "queue",
        "stats": stats,
        "posts": rows if view != "timeline" else [],
        "counts": counts,
        "status_filter": status_filter,
        "type_filter": type_filter,
        "view": view,
        "timeline_weeks": timeline_weeks,
    })


@router.get("/queue/{post_id}", response_class=HTMLResponse)
async def queue_detail(request: Request, post_id: int):
    from web.routes.dashboard import _global_stats

    post = await query_one("SELECT * FROM content_queue WHERE id = ?", (post_id,))
    if not post:
        return HTMLResponse("<h1>Post not found</h1>", status_code=404)

    # Get performance if published
    perf = None
    if post.get("instagram_media_id"):
        perf = await query_one(
            "SELECT * FROM post_performance WHERE content_queue_id = ? "
            "ORDER BY measured_at DESC LIMIT 1",
            (post_id,),
        )

    stats = await _global_stats()

    return templates.TemplateResponse(request, "pages/queue_detail.html", {
        "active_page": "queue",
        "stats": stats,
        "post": post,
        "perf": perf,
    })


def _do_approve(post_id: int):
    """Synchronous approval: upscale and update DB."""
    from db.connection import get_db
    db = get_db()
    row = db.execute(
        "SELECT image_url FROM content_queue WHERE id = ? AND status = 'pending_approval'",
        (post_id,),
    ).fetchone()
    if not row:
        return
    try:
        from tools.image_gen import upscale_and_host
        final_url = upscale_and_host(row["image_url"])
    except Exception:
        final_url = row["image_url"]
    db.execute(
        "UPDATE content_queue SET status = 'approved', approved_by = 'dashboard', "
        "approved_at = CURRENT_TIMESTAMP, image_url = ? WHERE id = ?",
        (final_url, post_id),
    )
    db.commit()


@router.post("/queue/{post_id}/approve", response_class=HTMLResponse)
async def approve_post(post_id: int, background_tasks: BackgroundTasks):
    post = await query_one(
        "SELECT status FROM content_queue WHERE id = ?", (post_id,)
    )
    if not post or post["status"] != "pending_approval":
        return HTMLResponse('<span class="badge badge-failed">Not pending</span>')

    # Mark as approved immediately
    await execute(
        "UPDATE content_queue SET status = 'approved', approved_by = 'dashboard', "
        "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (post_id,),
    )

    # Upscale in background
    background_tasks.add_task(_do_approve, post_id)

    return HTMLResponse('<span class="badge badge-approved">Approved</span>')


@router.post("/queue/{post_id}/reject", response_class=HTMLResponse)
async def reject_post(post_id: int):
    await execute(
        "UPDATE content_queue SET status = 'rejected' WHERE id = ? AND status = 'pending_approval'",
        (post_id,),
    )
    return HTMLResponse('<span class="badge badge-rejected">Rejected</span>')


@router.post("/queue/{post_id}/requeue", response_class=HTMLResponse)
async def requeue_post(post_id: int):
    post = await query_one(
        "SELECT status, image_url FROM content_queue WHERE id = ?", (post_id,)
    )
    if not post or post["status"] != "rejected":
        return HTMLResponse('<span class="badge badge-failed">Not rejected</span>')

    new_status = "pending_approval" if post.get("image_url") else "draft"
    await execute(
        "UPDATE content_queue SET status = ? WHERE id = ?",
        (new_status, post_id),
    )
    return HTMLResponse(f'<span class="badge badge-{new_status}">{new_status.replace("_", " ")}</span>')


@router.post("/queue/{post_id}/edit-caption", response_class=HTMLResponse)
async def edit_caption(post_id: int, caption: str = Form("")):
    post = await query_one("SELECT status FROM content_queue WHERE id = ?", (post_id,))
    if not post or post["status"] not in ("pending_approval", "draft", "approved"):
        return HTMLResponse('<div class="detail-field"><div class="detail-field-label">Caption</div>'
                            '<div class="detail-field-value text-sm" style="color:var(--status-failed)">Cannot edit.</div></div>')

    await execute("UPDATE content_queue SET caption = ? WHERE id = ?", (caption, post_id))
    from html import escape
    escaped = escape(caption)
    return HTMLResponse(
        f'<div class="detail-field" id="caption-field">'
        f'<div class="detail-field-label">Caption <span class="text-xs text-muted" style="margin-left:8px;">Saved</span></div>'
        f'<div class="detail-field-value rtl pre-wrap">{escaped}</div>'
        f'</div>'
    )


@router.post("/queue/{post_id}/edit-hashtags", response_class=HTMLResponse)
async def edit_hashtags(post_id: int, hashtags: str = Form("")):
    post = await query_one("SELECT status FROM content_queue WHERE id = ?", (post_id,))
    if not post or post["status"] not in ("pending_approval", "draft", "approved"):
        return HTMLResponse('<div class="detail-field"><div class="detail-field-label">Hashtags</div>'
                            '<div class="detail-field-value text-sm" style="color:var(--status-failed)">Cannot edit.</div></div>')

    await execute("UPDATE content_queue SET hashtags = ? WHERE id = ?", (hashtags, post_id))
    from html import escape
    escaped = escape(hashtags)
    return HTMLResponse(
        f'<div class="detail-field" id="hashtags-field">'
        f'<div class="detail-field-label">Hashtags <span class="text-xs text-muted" style="margin-left:8px;">Saved</span></div>'
        f'<div class="detail-field-value rtl text-sm">{escaped}</div>'
        f'</div>'
    )


def _do_direct_regen(post_id: int, direction: str, content_pillar: str):
    """Synchronous: generate image from user direction + LLM caption, update post."""
    from db.connection import get_db
    from tools.content_guide import build_image_prompt
    from tools.image_gen import generate_one
    from config import get_llm

    db = get_db()

    # Generate image
    prompt = build_image_prompt.invoke(direction)
    image_url = generate_one(prompt)

    # Generate caption
    llm = get_llm(temperature=0.7)
    response = llm.invoke(
        "You write Instagram captions for Capa & Co (קאפה אנד קו), an Israeli bakery.\n\n"
        "Rules:\n"
        "- Write in native Israeli Hebrew. Not translated. Not corporate.\n"
        "- Short and playful — one line is best, max 2 short sentences.\n"
        "- The caption MUST be specifically about the dish/image described below.\n"
        "- Add 3-5 hashtags at the end (mix Hebrew and English).\n"
        "- Emojis: max one, only if natural.\n\n"
        "Examples of good captions:\n"
        '- "אפשר להריח את החמאה דרך הטלפון :) #קאפהאנדקו #croissant #בייקרי"\n'
        '- "כריך שהוא מעט יווני והמון ישראלי #קאפהאנדקו #halloumi #כריכים"\n\n'
        f"Content pillar: {content_pillar}\n"
        f"Visual direction: {direction}\n\n"
        "Write ONLY the caption. Nothing else."
    )
    caption = response.content.strip()

    # Update post
    db.execute(
        "UPDATE content_queue SET image_url = ?, caption = ?, visual_direction = ?, "
        "topic = ?, status = 'pending_approval' WHERE id = ?",
        (image_url, caption, direction, direction, post_id),
    )
    db.commit()
    log.info(f"Post {post_id} direct-regen complete: direction='{direction}', image={image_url}")


@router.post("/queue/{post_id}/direct-regen", response_class=HTMLResponse)
async def direct_regen_post(
    post_id: int,
    background_tasks: BackgroundTasks,
    direction: str = Form(""),
):
    if not direction.strip():
        return HTMLResponse('<div class="text-sm" style="color:var(--status-failed)">Please enter a direction.</div>')

    post = await query_one("SELECT id, content_pillar FROM content_queue WHERE id = ?", (post_id,))
    if not post:
        return HTMLResponse('<div class="text-sm" style="color:var(--status-failed)">Post not found.</div>')

    background_tasks.add_task(
        _do_direct_regen, post_id, direction.strip(), post.get("content_pillar", "product")
    )

    return HTMLResponse(
        f'<div class="text-sm" style="color:var(--brand-green)">'
        f'Generating image for "<strong>{direction.strip()}</strong>" with a matching caption... '
        f'Refresh the page in a minute to see the result.</div>'
    )


def _generate_suggestions(post: dict) -> list[dict]:
    """Use the LLM to generate 3 alternative post suggestions."""
    from config import get_llm
    from tools.content_guide import get_menu_items

    menu = get_menu_items()
    menu_str = "\n".join(f"  {cat}: {', '.join(items)}" for cat, items in menu.items())

    llm = get_llm(temperature=0.8)
    response = llm.invoke(
        f"""You are the content strategist for Capa & Co (קאפה אנד קו), a B2B sandwich supplier in Israel.

A post was rejected and needs replacement. Here's the rejected post:
- Topic: {post.get('topic', '')}
- Caption: {post.get('caption', '')}
- Visual direction: {post.get('visual_direction', '')}
- Content type: {post['content_type']}
- Content pillar: {post.get('content_pillar', '')}

Suggest 3 DIFFERENT replacement posts. Each must use a different visual_direction from the menu.
Captions MUST be in native Israeli Hebrew — short, warm, playful. Max 1-2 sentences.

AVAILABLE MENU ITEMS (use these EXACT names for visual_direction):
{menu_str}

Respond ONLY with a JSON array of 3 objects, each with: topic, caption, hashtags, visual_direction, content_pillar.
No markdown, no explanation, just the JSON array."""
    )

    try:
        text = response.content.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        return json.loads(text)
    except (json.JSONDecodeError, IndexError):
        log.error(f"Failed to parse LLM suggestions: {response.content[:300]}")
        return []


@router.post("/queue/{post_id}/suggestions", response_class=HTMLResponse)
async def get_suggestions(post_id: int):
    post = await query_one("SELECT * FROM content_queue WHERE id = ?", (post_id,))
    if not post:
        return HTMLResponse('<div class="text-sm text-muted">Post not found.</div>')

    loop = asyncio.get_event_loop()
    suggestions = await loop.run_in_executor(None, partial(_generate_suggestions, post))

    if not suggestions:
        return HTMLResponse('<div class="text-sm" style="color:var(--status-failed)">Failed to generate suggestions. Try again.</div>')

    from html import escape

    html = '<div class="section-label">Pick a replacement</div>'
    for i, s in enumerate(suggestions):
        topic = escape(s.get("topic", ""))
        caption = escape(s.get("caption", ""))
        hashtags = escape(s.get("hashtags", ""))
        visual = escape(s.get("visual_direction", ""))
        pillar = escape(s.get("content_pillar", ""))
        html += f"""
        <div class="card mb-4" style="padding:16px">
            <div class="text-sm" style="font-weight:600">{i+1}. {topic}</div>
            <div class="text-sm rtl mt-2" style="line-height:1.6">{caption}</div>
            <div class="text-xs text-muted mt-2">{visual} &middot; {pillar.replace('_', ' ')}</div>
            <div class="text-xs text-muted">{hashtags}</div>
            <form class="mt-4" hx-post="/queue/{post_id}/regenerate"
                  hx-target="#regen-area" hx-swap="innerHTML"
                  hx-confirm="Regenerate with this option?">
                <input type="hidden" name="topic" value="{topic}">
                <input type="hidden" name="caption" value="{caption}">
                <input type="hidden" name="hashtags" value="{hashtags}">
                <input type="hidden" name="visual_direction" value="{visual}">
                <input type="hidden" name="content_pillar" value="{pillar}">
                <button type="submit" class="btn btn-primary btn-xs">Use This</button>
            </form>
        </div>"""

    return HTMLResponse(html)


def _do_regenerate(post_id: int, topic: str, caption: str, hashtags: str,
                   visual_direction: str, content_pillar: str):
    """Synchronous: update post fields, generate new image, set to pending_approval."""
    from db.connection import get_db
    from tools.content_guide import build_image_prompt
    from tools.image_gen import generate_one

    db = get_db()

    # Update post content
    db.execute(
        "UPDATE content_queue SET topic = ?, caption = ?, hashtags = ?, "
        "visual_direction = ?, content_pillar = ?, status = 'draft', "
        "image_url = NULL WHERE id = ?",
        (topic, caption, hashtags, visual_direction, content_pillar, post_id),
    )
    db.commit()

    # Generate new image
    try:
        prompt = build_image_prompt.invoke(visual_direction)
        image_url = generate_one(prompt)
        db.execute(
            "UPDATE content_queue SET image_url = ?, status = 'pending_approval' WHERE id = ?",
            (image_url, post_id),
        )
        db.commit()
        log.info(f"Post {post_id} regenerated with new image: {image_url}")
    except Exception as e:
        log.error(f"Post {post_id} regeneration image failed: {e}")
        db.execute(
            "UPDATE content_queue SET status = 'draft' WHERE id = ?",
            (post_id,),
        )
        db.commit()


@router.post("/queue/{post_id}/regenerate", response_class=HTMLResponse)
async def regenerate_post(
    post_id: int,
    background_tasks: BackgroundTasks,
    topic: str = Form(""),
    caption: str = Form(""),
    hashtags: str = Form(""),
    visual_direction: str = Form(""),
    content_pillar: str = Form(""),
):
    post = await query_one("SELECT id FROM content_queue WHERE id = ?", (post_id,))
    if not post:
        return HTMLResponse('<div class="text-sm" style="color:var(--status-failed)">Post not found.</div>')

    background_tasks.add_task(
        _do_regenerate, post_id, topic, caption, hashtags, visual_direction, content_pillar
    )

    return HTMLResponse(
        '<div class="text-sm" style="color:var(--brand-green)">'
        'Regenerating image... This may take a minute. Refresh the page to see the result.</div>'
    )
