"""Deterministic content publisher — no LLM involved.

Queries due approved posts, publishes them to Instagram, and updates the DB.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import brands.loader
from brands.loader import brand_config
from db.connection import get_db
from tools.instagram import publish_photo_post, publish_story, _published_today

log = logging.getLogger("capaco")


def publish_one(post_id: int, brand_slug: str | None = None) -> tuple[bool, str]:
    """Publish a single post immediately, bypassing schedule and daily-limit gates.

    This is the on-demand counterpart to publish_due_posts() — used by the
    "Publish Now" button in the dashboard and Telegram. It still enforces
    idempotency via instagram_media_id so double-clicks can't double-post.

    Returns (success, human-readable message).
    """
    if brand_slug:
        brands.loader.set_brand(brand_slug)

    db = get_db()
    row = db.execute(
        "SELECT id, image_url, caption, hashtags, content_type, "
        "instagram_media_id, status, topic FROM content_queue "
        "WHERE id = ? AND brand_id = ?",
        (post_id, brands.loader.brand_config.slug),
    ).fetchone()

    if not row:
        return False, f"Post {post_id} not found."
    if row["instagram_media_id"]:
        return False, f"Post {post_id} already published to Instagram."
    if row["status"] not in ("approved", "failed"):
        return False, f"Post {post_id} is '{row['status']}', not publishable."
    if not row["image_url"]:
        return False, f"Post {post_id} has no image_url."

    content_type = row["content_type"]
    try:
        if content_type == "photo":
            caption = ((row["caption"] or "") + "\n" + (row["hashtags"] or "")).strip()
            result = publish_photo_post.invoke(
                {"image_url": row["image_url"], "caption": caption}
            )
        else:
            result = publish_story.invoke({"image_url": row["image_url"]})

        media_id = result.get("id", "")
        db.execute(
            "UPDATE content_queue SET status = 'published', instagram_media_id = ?, "
            "published_at = CURRENT_TIMESTAMP WHERE id = ?",
            (media_id, post_id),
        )
        db.commit()
        log.info(f"Published {content_type} post {post_id} on demand (media_id={media_id})")
        return True, f"Published (media_id={media_id})"
    except Exception as e:
        db.execute(
            "UPDATE content_queue SET status = 'failed', "
            "retry_count = retry_count + 1 WHERE id = ?",
            (post_id,),
        )
        db.commit()
        log.error(f"On-demand publish failed for post {post_id}: {e}")
        return False, f"Publish failed: {e}"


def publish_due_posts(content_type: str) -> str:
    """Publish all due approved posts of the given content type. Returns a summary string."""
    tz = ZoneInfo(brand_config.identity.timezone)
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    now_time = now.strftime("%H:%M")

    db = get_db()

    rows = db.execute(
        "SELECT id, image_url, caption, hashtags, content_type, instagram_media_id "
        "FROM content_queue "
        "WHERE status = 'approved' AND content_type = ? AND brand_id = ? AND image_url IS NOT NULL "
        "AND (scheduled_date < ? OR (scheduled_date = ? AND scheduled_time <= ?)) "
        "ORDER BY scheduled_date ASC, scheduled_time ASC",
        (content_type, brand_config.slug, today, today, now_time),
    ).fetchall()

    if not rows:
        return f"No due {content_type} posts to publish."

    max_posts = brand_config.content_strategy.max_posts_per_day
    max_stories = brand_config.content_strategy.max_stories_per_day
    max_per_day = max_posts if content_type == "photo" else max_stories
    already_published = _published_today(content_type)

    published = 0
    failed = 0
    skipped = 0

    for post in rows:
        # Idempotency: skip if already pushed to Instagram
        if post["instagram_media_id"]:
            skipped += 1
            continue

        # Daily limit (account for what we've published in this run too)
        if already_published + published >= max_per_day:
            log.info(f"Daily {content_type} limit reached ({max_per_day}), stopping.")
            break

        post_id = post["id"]
        try:
            if content_type == "photo":
                caption = ((post["caption"] or "") + "\n" + (post["hashtags"] or "")).strip()
                result = publish_photo_post.invoke({"image_url": post["image_url"], "caption": caption})
            else:
                result = publish_story.invoke({"image_url": post["image_url"]})

            media_id = result.get("id", "")
            db.execute(
                "UPDATE content_queue SET status = 'published', instagram_media_id = ?, "
                "published_at = CURRENT_TIMESTAMP WHERE id = ?",
                (media_id, post_id),
            )
            db.commit()
            published += 1
            log.info(f"Published {content_type} post {post_id} (media_id={media_id})")

        except Exception as e:
            db.execute(
                "UPDATE content_queue SET status = 'failed', "
                "retry_count = retry_count + 1 WHERE id = ?",
                (post_id,),
            )
            db.commit()
            failed += 1
            log.error(f"Failed to publish {content_type} post {post_id}: {e}")

    parts = []
    if published:
        parts.append(f"{published} published")
    if failed:
        parts.append(f"{failed} failed")
    if skipped:
        parts.append(f"{skipped} skipped (already on Instagram)")
    remaining = len(rows) - published - failed - skipped
    if remaining:
        parts.append(f"{remaining} deferred (daily limit)")
    return f"{content_type.title()} publish: {', '.join(parts)}." if parts else f"No {content_type} posts to publish."
