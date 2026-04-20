"""Content queue pages and actions."""

import asyncio
import json
import logging
from collections import OrderedDict
from datetime import date, timedelta
from functools import partial
from html import escape

from fastapi import APIRouter, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse

from web import templates
from web.db import query, query_one, execute
from web.brand_switcher import get_dashboard_brand, get_brand_context

router = APIRouter()
log = logging.getLogger("capaco")

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _render_detail_edit_error(field_id: str, label: str, *, wide: bool = False) -> HTMLResponse:
    block_classes = "queue-detail-block"
    if wide:
        block_classes += " queue-detail-block-wide"

    return HTMLResponse(
        f'<div class="{block_classes}" id="{field_id}">'
        f'<div class="queue-detail-block-header">'
        f'<div class="queue-detail-block-label">{label}</div>'
        f'</div>'
        f'<div class="queue-detail-value queue-detail-empty">Cannot edit.</div>'
        f'</div>'
    )


def _render_editable_detail_block(
    field_id: str,
    label: str,
    display_id: str,
    form_id: str,
    post_id: int,
    action_path: str,
    value_html: str,
    form_controls_html: str,
    *,
    wide: bool = False,
) -> HTMLResponse:
    block_classes = "queue-detail-block"
    if wide:
        block_classes += " queue-detail-block-wide"

    return HTMLResponse(
        f'<div class="{block_classes}" id="{field_id}">'
        f'<div class="queue-detail-block-header">'
        f'<div class="queue-detail-block-label">{label}</div>'
        f'<div style="display:flex;align-items:center;gap:8px;">'
        f'<span class="queue-detail-note">Saved</span>'
        f'<button class="btn btn-outline btn-xs queue-detail-edit" '
        f'onclick="document.getElementById(\'{display_id}\').style.display=\'none\'; '
        f'document.getElementById(\'{form_id}\').style.display=\'block\';">Edit</button>'
        f'</div>'
        f'</div>'
        f'{value_html}'
        f'<form id="{form_id}" class="queue-detail-form" '
        f'hx-post="/queue/{post_id}/{action_path}" '
        f'hx-target="#{field_id}" '
        f'hx-swap="outerHTML" '
        f'hx-disabled-elt="find button[type=\'submit\']">'
        f'{form_controls_html}'
        f'<div class="queue-detail-form-actions">'
        f'<button type="submit" class="btn btn-primary btn-xs">'
        f'<span class="loading loading-spinner loading-xs htmx-indicator"></span> Save'
        f'</button>'
        f'<button type="button" class="btn btn-outline btn-xs" '
        f'onclick="document.getElementById(\'{display_id}\').style.display=\'\'; '
        f'document.getElementById(\'{form_id}\').style.display=\'none\';">Cancel</button>'
        f'</div>'
        f'</form>'
        f'</div>'
    )


@router.get("/queue", response_class=HTMLResponse)
async def queue_page(request: Request):
    from web.routes.dashboard import _global_stats

    brand_id = get_dashboard_brand(request)
    view = request.query_params.get("view", "timeline")
    status_filter = request.query_params.get("status", "")
    type_filter = request.query_params.get("type", "")

    # Build query
    conditions = ["brand_id = ?"]
    params = [brand_id]
    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)
    if type_filter:
        conditions.append("content_type = ?")
        params.append(type_filter)

    where = "WHERE " + " AND ".join(conditions)
    rows = await query(
        f"SELECT id, scheduled_date, scheduled_time, content_type, content_pillar, "
        f"topic, status, image_url FROM content_queue {where} "
        f"ORDER BY scheduled_date ASC, scheduled_time ASC LIMIT 200",
        tuple(params),
    )

    # Status counts for filter pills
    counts_rows = await query(
        "SELECT status, COUNT(*) as count FROM content_queue WHERE brand_id = ? GROUP BY status",
        (brand_id,),
    )
    counts = {r["status"]: r["count"] for r in counts_rows}

    stats = await _global_stats(brand_id)

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
        **get_brand_context(request),
    })


@router.get("/queue/{post_id}", response_class=HTMLResponse)
async def queue_detail(request: Request, post_id: int):
    from web.routes.dashboard import _global_stats

    brand_id = get_dashboard_brand(request)

    post = await query_one(
        "SELECT * FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse("<h1>Post not found</h1>", status_code=404)

    # Get performance if published
    perf = None
    if post.get("instagram_media_id"):
        perf = await query_one(
            "SELECT * FROM post_performance WHERE content_queue_id = ? AND brand_id = ? "
            "ORDER BY measured_at DESC LIMIT 1",
            (post_id, brand_id),
        )

    stats = await _global_stats(brand_id)

    return templates.TemplateResponse(request, "pages/queue_detail.html", {
        "active_page": "queue",
        "stats": stats,
        "post": post,
        "perf": perf,
        **get_brand_context(request),
    })


def _do_approve(post_id: int):
    """Synchronous approval: upscale and update DB."""
    from db.connection import get_db
    db = get_db()
    row = db.execute(
        "SELECT image_url FROM content_queue WHERE id = ? AND status IN ('pending_approval', 'draft')",
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
async def approve_post(request: Request, post_id: int, background_tasks: BackgroundTasks):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post or post["status"] not in ("pending_approval", "draft"):
        return HTMLResponse('<span class="badge badge-failed">Not pending</span>')

    # Mark as approved immediately
    await execute(
        "UPDATE content_queue SET status = 'approved', approved_by = 'dashboard', "
        "approved_at = CURRENT_TIMESTAMP WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )

    # Upscale in background
    background_tasks.add_task(_do_approve, post_id)

    return HTMLResponse('<span class="badge badge-approved">Approved</span>')


@router.post("/queue/{post_id}/reject", response_class=HTMLResponse)
async def reject_post(request: Request, post_id: int):
    brand_id = get_dashboard_brand(request)
    await execute(
        "UPDATE content_queue SET status = 'rejected' WHERE id = ? AND status = 'pending_approval' AND brand_id = ?",
        (post_id, brand_id),
    )
    return HTMLResponse('<span class="badge badge-rejected">Rejected</span>')


@router.post("/queue/{post_id}/republish", response_class=HTMLResponse)
async def republish_post(request: Request, post_id: int):
    """Re-queue a failed post for publishing.

    Resets status to 'approved' and retry_count to 0. The image has already
    been upscaled (that happens at approval), so we don't need to rerun it.
    The WHERE clause gates on status='failed' so double-clicks are no-ops.
    """
    brand_id = get_dashboard_brand(request)
    await execute(
        "UPDATE content_queue SET status = 'approved', retry_count = 0 "
        "WHERE id = ? AND status = 'failed' AND brand_id = ?",
        (post_id, brand_id),
    )
    return HTMLResponse('<span class="badge badge-approved">Re-queued</span>')


def _run_publish_now(post_id: int, brand_slug: str) -> None:
    """BackgroundTasks entry point — publish_one updates the post's status
    on both success and failure, so the outcome is visible via the dashboard
    even if this raises."""
    from agents.content_publisher import publish_one

    try:
        publish_one(post_id, brand_slug=brand_slug)
    except Exception as e:
        log.exception(f"publish_now background task crashed for post {post_id}: {e}")


@router.post("/queue/{post_id}/publish-now", response_class=HTMLResponse)
async def publish_now(request: Request, post_id: int, background_tasks: BackgroundTasks):
    """Publish a post to Instagram immediately, bypassing the scheduler.

    Runs the actual publish in a background task because the Instagram API
    round-trip can take 5–15s — we don't want to block the HTMX swap on it.
    """
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status, image_url FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<span class="badge badge-failed">Not found</span>')
    if post["status"] not in ("approved", "failed"):
        return HTMLResponse(
            f'<span class="badge badge-failed">Not publishable ({post["status"]})</span>'
        )
    if not post["image_url"]:
        return HTMLResponse('<span class="badge badge-failed">No image</span>')

    background_tasks.add_task(_run_publish_now, post_id, brand_id)
    return HTMLResponse('<span class="badge badge-approved">Publishing…</span>')


@router.post("/queue/{post_id}/convert-type", response_class=HTMLResponse)
async def convert_content_type(request: Request, post_id: int):
    """Toggle a post between feed photo and story formats.

    Not allowed for already-published posts."""
    from fastapi.responses import Response

    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status, content_type FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<div class="text-sm text-error">Post not found.</div>', status_code=404)
    if post["status"] == "published":
        return HTMLResponse(
            '<div class="text-sm text-error">Cannot convert an already-published post.</div>',
            status_code=400,
        )

    current = (post.get("content_type") or "").lower()
    # Anything not 'story' flips to 'story'; 'story' flips back to 'photo'.
    new_type = "photo" if current == "story" else "story"

    await execute(
        "UPDATE content_queue SET content_type = ? WHERE id = ? AND brand_id = ?",
        (new_type, post_id, brand_id),
    )

    # Tell htmx to refresh the page so every reference to content_type updates
    # (chips, filters, badges, conditional blocks).
    return Response(status_code=204, headers={"HX-Refresh": "true"})


@router.post("/queue/{post_id}/requeue", response_class=HTMLResponse)
async def requeue_post(request: Request, post_id: int):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status, image_url FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post or post["status"] != "rejected":
        return HTMLResponse('<span class="badge badge-failed">Not rejected</span>')

    new_status = "pending_approval" if post.get("image_url") else "draft"
    await execute(
        "UPDATE content_queue SET status = ? WHERE id = ? AND brand_id = ?",
        (new_status, post_id, brand_id),
    )
    return HTMLResponse(f'<span class="badge badge-{new_status}">{new_status.replace("_", " ")}</span>')


@router.post("/queue/{post_id}/edit-schedule", response_class=HTMLResponse)
async def edit_schedule(request: Request, post_id: int, scheduled_date: str = Form(""), scheduled_time: str = Form("")):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post or post["status"] not in ("pending_approval", "draft", "approved"):
        return _render_detail_edit_error("schedule-field", "Schedule")

    await execute(
        "UPDATE content_queue SET scheduled_date = ?, scheduled_time = ? WHERE id = ? AND brand_id = ?",
        (scheduled_date, scheduled_time, post_id, brand_id),
    )
    schedule_value = "—"
    schedule_classes = "queue-detail-value"
    if scheduled_date or scheduled_time:
        schedule_value = f'{escape(scheduled_date or "—")}{(" " + escape(scheduled_time)) if scheduled_time else ""}'
    else:
        schedule_classes += " queue-detail-empty"
    return _render_editable_detail_block(
        "schedule-field",
        "Schedule",
        "schedule-display",
        "schedule-edit",
        post_id,
        "edit-schedule",
        f'<div class="{schedule_classes}" id="schedule-display">{schedule_value}</div>',
        f'<div class="queue-detail-form-row">'
        f'<input type="date" name="scheduled_date" value="{escape(scheduled_date)}" class="input input-bordered input-sm">'
        f'<input type="time" name="scheduled_time" value="{escape(scheduled_time)}" class="input input-bordered input-sm">'
        f'</div>',
    )


@router.post("/queue/{post_id}/edit-caption", response_class=HTMLResponse)
async def edit_caption(request: Request, post_id: int, caption: str = Form("")):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post or post["status"] not in ("pending_approval", "draft", "approved"):
        return _render_detail_edit_error("caption-field", "Caption", wide=True)

    await execute(
        "UPDATE content_queue SET caption = ? WHERE id = ? AND brand_id = ?",
        (caption, post_id, brand_id),
    )
    escaped = escape(caption)
    caption_value = escaped if escaped else "—"
    caption_classes = "queue-detail-value whitespace-pre-wrap break-words"
    if not escaped:
        caption_classes += " queue-detail-empty"
    return _render_editable_detail_block(
        "caption-field",
        "Caption",
        "caption-display",
        "caption-edit",
        post_id,
        "edit-caption",
        f'<div class="{caption_classes}" dir="rtl" id="caption-display">{caption_value}</div>',
        f'<textarea name="caption" rows="4" dir="rtl" class="textarea textarea-bordered text-sm">{escaped}</textarea>',
        wide=True,
    )


@router.post("/queue/{post_id}/edit-hashtags", response_class=HTMLResponse)
async def edit_hashtags(request: Request, post_id: int, hashtags: str = Form("")):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post or post["status"] not in ("pending_approval", "draft", "approved"):
        return _render_detail_edit_error("hashtags-field", "Hashtags", wide=True)

    await execute(
        "UPDATE content_queue SET hashtags = ? WHERE id = ? AND brand_id = ?",
        (hashtags, post_id, brand_id),
    )
    escaped = escape(hashtags)
    hashtags_value = escaped if escaped else "—"
    hashtags_classes = "queue-detail-value"
    if not escaped:
        hashtags_classes += " queue-detail-empty"
    return _render_editable_detail_block(
        "hashtags-field",
        "Hashtags",
        "hashtags-display",
        "hashtags-edit",
        post_id,
        "edit-hashtags",
        f'<div class="{hashtags_classes}" dir="rtl" id="hashtags-display">{hashtags_value}</div>',
        f'<input type="text" name="hashtags" dir="rtl" value="{escaped}" class="input input-bordered input-sm">',
        wide=True,
    )


@router.get("/queue/{post_id}/poll-status")
async def poll_post_status(request: Request, post_id: int):
    """Lightweight JSON endpoint for polling post generation status."""
    from fastapi.responses import JSONResponse

    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT status, image_url, caption, topic, hashtags, visual_direction, content_pillar "
        "FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return JSONResponse({"status": "not_found"}, status_code=404)

    return JSONResponse({
        "status": post["status"],
        "image_url": post.get("image_url") or "",
        "caption": post.get("caption") or "",
        "topic": post.get("topic") or "",
        "hashtags": post.get("hashtags") or "",
        "visual_direction": post.get("visual_direction") or "",
        "content_pillar": post.get("content_pillar") or "",
    })


def _do_direct_regen(post_id: int, direction: str, content_pillar: str,
                     brand_id: str = "", original_direction: str = "",
                     reference_url: str | None = None):
    """Synchronous: generate image from user direction + LLM caption, update post."""
    from db.connection import get_db
    from tools.content_guide import build_image_prompt
    from tools.image_gen import generate_one
    from config import get_llm

    # Switch to correct brand context so prompts, visual config, and env vars match
    if brand_id:
        from brands.loader import set_brand
        bc = set_brand(brand_id)
    else:
        from brands.loader import brand_config as bc

    db = get_db()

    ct_row = db.execute("SELECT content_type FROM content_queue WHERE id = ?", (post_id,)).fetchone()
    content_type = (ct_row["content_type"] if ct_row else None) or "photo"

    # Mark as draft immediately so the UI reflects that regeneration is in progress
    db.execute(
        "UPDATE content_queue SET status = 'draft', image_url = NULL WHERE id = ?",
        (post_id,),
    )
    db.commit()

    try:
        # When a reference image is supplied, the edit endpoint uses it as the
        # primary visual anchor. Skip the LLM merge with the prior direction
        # (which would Frankenstein an unrelated composition onto the ref) and
        # skip the heavy brand-template wrapping (which would override the ref's
        # style). Use the user's direction verbatim with a minimal instruction.
        if reference_url:
            from tools.content_guide import build_reference_edit_prompt
            effective_direction = direction
            prompt = build_reference_edit_prompt(direction)
        else:
            # If we have an original direction, use LLM to merge the original with user feedback
            # so that "keep it, just make it more golden" refines the original prompt
            # instead of replacing it entirely.
            if original_direction and original_direction.strip() != direction:
                llm = get_llm(temperature=0.3)
                merged = llm.invoke(
                    "You refine image-generation prompts.\n\n"
                    f"Original visual direction:\n{original_direction}\n\n"
                    f"User feedback / modification request:\n{direction}\n\n"
                    "Write a single updated visual direction that keeps the original subject "
                    "and composition but incorporates the user's requested changes. "
                    "Output ONLY the updated direction, nothing else."
                )
                effective_direction = merged.content.strip()
            else:
                effective_direction = direction
            prompt = build_image_prompt.invoke(effective_direction)

        image_url = generate_one(prompt, reference_url=reference_url, content_type=content_type)

        # Generate caption using brand voice
        brand_name = bc.identity.name or brand_id
        tone = bc.voice.tone or "Warm, playful, polished"
        caption_style = bc.voice.caption_style or "Short and playful — one line is best, max 2 short sentences."
        examples = bc.voice.caption_examples or []
        examples_str = "\n".join(f'- "{ex}"' for ex in examples[:3])
        hashtags_default = " ".join(bc.voice.hashtags_default[:5]) if bc.voice.hashtags_default else ""

        llm = get_llm(temperature=0.7)
        response = llm.invoke(
            f"You write Instagram captions for {brand_name}.\n"
            f"Tone: {tone}\n\n"
            f"Rules:\n"
            f"- {caption_style}\n"
            f"- The caption MUST be specifically about the dish/image described below.\n"
            f"- Add 3-5 hashtags at the end (include brand hashtags like {hashtags_default}).\n"
            f"- Emojis: max one, only if natural.\n\n"
            + (f"Examples of good captions:\n{examples_str}\n\n" if examples_str else "")
            + f"Content pillar: {content_pillar}\n"
            f"Visual direction: {effective_direction}\n\n"
            f"Write ONLY the caption. Nothing else."
        )
        caption = response.content.strip()

        # Update post
        db.execute(
            "UPDATE content_queue SET image_url = ?, caption = ?, visual_direction = ?, "
            "topic = ?, status = 'pending_approval' WHERE id = ?",
            (image_url, caption, effective_direction, effective_direction, post_id),
        )
        db.commit()
        log.info(f"Post {post_id} direct-regen complete: direction='{effective_direction}', image={image_url}")
    except Exception as e:
        log.error(f"Post {post_id} direct-regen failed: {e}")
        db.execute(
            "UPDATE content_queue SET status = 'draft' WHERE id = ?",
            (post_id,),
        )
        db.commit()


@router.post("/queue/{post_id}/direct-regen", response_class=HTMLResponse)
async def direct_regen_post(
    request: Request,
    post_id: int,
    background_tasks: BackgroundTasks,
    direction: str = Form(""),
    reference_url: str = Form(""),
):
    if not direction.strip():
        return HTMLResponse('<div class="text-sm text-error">Please enter a direction.</div>')

    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT id, content_pillar, visual_direction FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<div class="text-sm text-error">Post not found.</div>')

    background_tasks.add_task(
        _do_direct_regen, post_id, direction.strip(), post.get("content_pillar", "product"),
        brand_id, post.get("visual_direction", ""),
        reference_url=reference_url.strip() or None,
    )

    return HTMLResponse(
        f'<div class="alert alert-success text-sm shadow-sm">'
        f'<span class="loading loading-spinner loading-sm"></span>'
        f'Generating image for "<strong>{direction.strip()}</strong>" with a matching caption...</div>'
    )


def _do_refine(post_id: int, direction: str, brand_id: str, reference_image: str):
    """Synchronous: iterative image-only refinement. Uses the post's current
    image as the reference, applies the user's short edit instruction, and
    updates ONLY image_url. Caption, visual_direction, and topic are left
    untouched — refine is a visual tweak, not a content rewrite."""
    from db.connection import get_db
    from tools.content_guide import build_reference_edit_prompt
    from tools.image_gen import generate_one

    if brand_id:
        from brands.loader import set_brand
        set_brand(brand_id)

    db = get_db()

    ct_row = db.execute("SELECT content_type FROM content_queue WHERE id = ?", (post_id,)).fetchone()
    content_type = (ct_row["content_type"] if ct_row else None) or "photo"

    # Keep the old image_url in place while generating so the UI can keep
    # showing the current image; only the status reflects the in-flight work.
    db.execute(
        "UPDATE content_queue SET status = 'draft' WHERE id = ?",
        (post_id,),
    )
    db.commit()

    try:
        prompt = build_reference_edit_prompt(direction)
        image_url = generate_one(prompt, reference_url=reference_image, content_type=content_type)

        db.execute(
            "UPDATE content_queue SET image_url = ?, status = 'pending_approval' WHERE id = ?",
            (image_url, post_id),
        )
        db.commit()
        log.info(f"Post {post_id} refined: '{direction}' → {image_url}")
    except Exception as e:
        log.error(f"Post {post_id} refine failed: {e}")
        db.execute(
            "UPDATE content_queue SET status = 'pending_approval' WHERE id = ?",
            (post_id,),
        )
        db.commit()


@router.post("/queue/{post_id}/refine", response_class=HTMLResponse)
async def refine_post(
    request: Request,
    post_id: int,
    background_tasks: BackgroundTasks,
    direction: str = Form(""),
):
    """Iterative refinement: use the post's current image as the reference
    and apply a short edit instruction on top of it. Each refine chains off
    the latest image, so "add vanilla beans" → "now add my logo" works."""
    if not direction.strip():
        return HTMLResponse('<div class="text-sm text-error">Please describe the change you want.</div>')

    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT id, image_url FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<div class="text-sm text-error">Post not found.</div>')

    current_image = (post.get("image_url") or "").strip()
    if not current_image:
        return HTMLResponse(
            '<div class="text-sm text-error">This post has no image yet — use Regen + Direction first.</div>'
        )

    background_tasks.add_task(
        _do_refine, post_id, direction.strip(), brand_id, current_image,
    )

    return HTMLResponse(
        f'<div class="alert alert-success text-sm shadow-sm">'
        f'<span class="loading loading-spinner loading-sm"></span>'
        f'Refining image: "<strong>{direction.strip()}</strong>"...</div>'
    )


def _generate_suggestions(post: dict) -> list[dict]:
    """Use the LLM to generate 3 alternative post suggestions."""
    from config import get_llm
    from tools.content_guide import get_menu_items
    from brands.loader import brand_config

    menu = get_menu_items()
    menu_str = "\n".join(f"  {cat}: {', '.join(items)}" for cat, items in menu.items())

    brand_name = brand_config.identity.name or brand_config.slug
    brand_type = brand_config.identity.business_type or "business"

    llm = get_llm(temperature=0.8)
    response = llm.invoke(
        f"""You are the content strategist for {brand_name}, a {brand_type} in Israel.

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
async def get_suggestions(request: Request, post_id: int):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT * FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<div class="text-sm opacity-50">Post not found.</div>')

    # Ensure brand context is set before generating suggestions
    from brands.loader import set_brand
    set_brand(brand_id)

    loop = asyncio.get_event_loop()
    suggestions = await loop.run_in_executor(None, partial(_generate_suggestions, post))

    if not suggestions:
        return HTMLResponse('<div class="text-sm text-error">Failed to generate suggestions. Try again.</div>')

    from html import escape

    html = '<h2 class="text-xs font-medium uppercase tracking-widest opacity-50 mb-3">Pick a replacement</h2>'
    for i, s in enumerate(suggestions):
        topic = escape(s.get("topic", ""))
        caption = escape(s.get("caption", ""))
        hashtags = escape(s.get("hashtags", ""))
        visual = escape(s.get("visual_direction", ""))
        pillar = escape(s.get("content_pillar", ""))
        html += f"""
        <div class="card bg-base-100 shadow-sm mb-4">
            <div class="card-body p-4">
                <div class="text-sm font-semibold">{i+1}. {topic}</div>
                <div class="text-sm mt-2" dir="rtl" style="line-height:1.6">{caption}</div>
                <div class="text-xs opacity-50 mt-2">{visual} &middot; {pillar.replace('_', ' ')}</div>
                <div class="text-xs opacity-50">{hashtags}</div>
                <form class="mt-3" hx-post="/queue/{post_id}/regenerate"
                      hx-target="#regen-area" hx-swap="innerHTML"
                      hx-indicator="#use-this-spinner-{i}"
                      hx-disabled-elt="find button[type='submit']">
                    <input type="hidden" name="topic" value="{topic}">
                    <input type="hidden" name="caption" value="{caption}">
                    <input type="hidden" name="hashtags" value="{hashtags}">
                    <input type="hidden" name="visual_direction" value="{visual}">
                    <input type="hidden" name="content_pillar" value="{pillar}">
                    <button type="submit" class="btn btn-primary btn-xs">
                        <span class="loading loading-spinner loading-xs htmx-indicator" id="use-this-spinner-{i}"></span>
                        Use This
                    </button>
                </form>
            </div>
        </div>"""

    return HTMLResponse(html)


def _do_regenerate(post_id: int, topic: str, caption: str, hashtags: str,
                   visual_direction: str, content_pillar: str, brand_id: str = ""):
    """Synchronous: update post fields, generate new image, set to pending_approval."""
    from db.connection import get_db
    from tools.content_guide import build_image_prompt
    from tools.image_gen import generate_one

    # Switch to correct brand context so prompts, visual config, and env vars match
    if brand_id:
        from brands.loader import set_brand
        set_brand(brand_id)

    db = get_db()

    ct_row = db.execute("SELECT content_type FROM content_queue WHERE id = ?", (post_id,)).fetchone()
    content_type = (ct_row["content_type"] if ct_row else None) or "photo"

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
        image_url = generate_one(prompt, content_type=content_type)
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
    request: Request,
    post_id: int,
    background_tasks: BackgroundTasks,
    topic: str = Form(""),
    caption: str = Form(""),
    hashtags: str = Form(""),
    visual_direction: str = Form(""),
    content_pillar: str = Form(""),
):
    brand_id = get_dashboard_brand(request)
    post = await query_one(
        "SELECT id FROM content_queue WHERE id = ? AND brand_id = ?",
        (post_id, brand_id),
    )
    if not post:
        return HTMLResponse('<div class="text-sm text-error">Post not found.</div>')

    background_tasks.add_task(
        _do_regenerate, post_id, topic, caption, hashtags, visual_direction, content_pillar,
        brand_id,
    )

    return HTMLResponse(
        '<div class="alert alert-success text-sm shadow-sm">'
        '<span class="loading loading-spinner loading-sm"></span>'
        'Regenerating image... The page will update automatically when ready.</div>'
    )
