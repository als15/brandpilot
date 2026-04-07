import json
from langchain_core.tools import tool
from db.connection import get_db


# ── Content Queue Tools ──────────────────────────────────────────────

@tool
def db_get_content_queue(status: str = "", limit: int = 10, due_only: bool = False) -> str:
    """Get posts from the content queue. Filter by status or get all.
    Args:
        status: Filter by status: 'draft', 'approved', 'published', 'failed'. Empty for all.
        limit: Max number of posts to return.
        due_only: If True, only return posts whose scheduled_date is today or earlier.
    """
    db = get_db()
    conditions = []
    params = []
    if status:
        conditions.append("status = ?")
        params.append(status)
    if due_only:
        conditions.append("scheduled_date <= date('now')")
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit)
    rows = db.execute(
        f"SELECT * FROM content_queue {where} ORDER BY scheduled_date DESC LIMIT ?",
        params,
    ).fetchall()
    if not rows:
        return f"No posts found{' with status=' + status if status else ''}."
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


@tool
def db_add_content_item(
    scheduled_date: str,
    scheduled_time: str,
    content_type: str,
    content_pillar: str,
    topic: str,
    caption: str,
    hashtags: str,
    visual_direction: str,
) -> str:
    """Add a new content item to the queue as a draft.
    Args:
        scheduled_date: Target date like '2026-04-07'.
        scheduled_time: Target time like '08:00'.
        content_type: Type: 'photo' or 'story'.
        content_pillar: One of: 'product', 'behind_scenes', 'customer_spotlight', 'industry_tips', 'social_proof'.
        topic: Short topic description.
        caption: Full caption text.
        hashtags: Hashtags as a string.
        visual_direction: Description of what the photo/video should show.
    """
    db = get_db()
    db.execute(
        """INSERT INTO content_queue
           (scheduled_date, scheduled_time, content_type, content_pillar, topic, caption, hashtags, visual_direction)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (scheduled_date, scheduled_time, content_type, content_pillar, topic, caption, hashtags, visual_direction),
    )
    db.commit()
    return f"Content item added: '{topic}' scheduled for {scheduled_date} at {scheduled_time}."


@tool
def db_update_post_status(post_id: int, status: str, instagram_media_id: str = "") -> str:
    """Update a content queue post's status.
    Args:
        post_id: The content queue item ID.
        status: New status: 'draft', 'pending_approval', 'approved', 'published', 'failed'.
        instagram_media_id: Instagram media ID after publishing (optional).
    """
    db = get_db()
    if status == "published":
        db.execute(
            "UPDATE content_queue SET status = ?, instagram_media_id = ?, published_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, instagram_media_id, post_id),
        )
    elif status == "failed":
        db.execute(
            "UPDATE content_queue SET status = ?, retry_count = COALESCE(retry_count, 0) + 1 WHERE id = ?",
            (status, post_id),
        )
    else:
        db.execute(
            "UPDATE content_queue SET status = ? WHERE id = ?",
            (status, post_id),
        )
    db.commit()
    return f"Post {post_id} status updated to '{status}'."


@tool
def db_revise_content_item(
    post_id: int,
    caption: str = "",
    hashtags: str = "",
    visual_direction: str = "",
    notes: str = "",
) -> str:
    """Revise a content queue item's caption, visual direction, hashtags, or add review notes.
    Only updates fields that are provided (non-empty).
    Args:
        post_id: The content queue item ID.
        caption: Revised caption text (optional).
        hashtags: Revised hashtags (optional).
        visual_direction: Revised image prompt direction (optional).
        notes: Design review notes or feedback (optional).
    """
    db = get_db()
    updates = []
    params = []
    if caption:
        updates.append("caption = ?")
        params.append(caption)
    if hashtags:
        updates.append("hashtags = ?")
        params.append(hashtags)
    if visual_direction:
        updates.append("visual_direction = ?")
        params.append(visual_direction)
    if notes:
        updates.append("notes = ?")
        params.append(notes)
    if not updates:
        return "No updates provided."
    params.append(post_id)
    db.execute(f"UPDATE content_queue SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return f"Post {post_id} revised: updated {', '.join(f.split(' =')[0] for f in updates)}."


# ── Leads Tools ──────────────────────────────────────────────────────

@tool
def db_get_leads(status: str = "", limit: int = 20) -> str:
    """Get leads from the database. Filter by status or get all.
    Args:
        status: Filter: 'discovered', 'researched', 'outreach_drafted', 'contacted', 'responded', 'converted'. Empty for all.
        limit: Max leads to return.
    """
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM leads WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM leads ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return f"No leads found{' with status=' + status if status else ''}."
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


@tool
def db_add_lead(
    business_name: str,
    business_type: str,
    source: str,
    instagram_handle: str = "",
    location: str = "",
    follower_count: int = 0,
    notes: str = "",
) -> str:
    """Add a new lead to the database.
    Args:
        business_name: Name of the business.
        business_type: Type: 'food_truck', 'coffee_shop', 'restaurant', 'catering'.
        source: How we found them (e.g. 'tavily_search', 'instagram_discovery').
        instagram_handle: Their Instagram handle (optional).
        location: Business location (optional).
        follower_count: Their Instagram follower count (optional).
        notes: Additional notes (optional).
    """
    db = get_db()
    # Check for duplicate by name
    existing = db.execute(
        "SELECT id FROM leads WHERE business_name = ?", (business_name,)
    ).fetchone()
    if existing:
        return f"Lead '{business_name}' already exists (id={existing['id']}). Use db_update_lead to update."
    db.execute(
        """INSERT INTO leads (business_name, business_type, source, instagram_handle, location, follower_count, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (business_name, business_type, source, instagram_handle, location, follower_count, notes),
    )
    db.commit()
    return f"Lead added: '{business_name}' ({business_type})."


@tool
def db_update_lead(lead_id: int, status: str = "", outreach_message: str = "", notes: str = "") -> str:
    """Update a lead's status, outreach message, or notes.
    Args:
        lead_id: The lead ID.
        status: New status (optional).
        outreach_message: Draft outreach message (optional).
        notes: Additional notes (optional).
    """
    db = get_db()
    updates = []
    params = []
    if status:
        updates.append("status = ?")
        params.append(status)
    if outreach_message:
        updates.append("outreach_message = ?")
        params.append(outreach_message)
    if notes:
        updates.append("notes = ?")
        params.append(notes)
    if not updates:
        return "No updates provided."
    updates.append("last_updated = CURRENT_TIMESTAMP")
    params.append(lead_id)
    db.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = ?", params)
    db.commit()
    return f"Lead {lead_id} updated."


# ── Analytics Tools ──────────────────────────────────────────────────

@tool
def db_get_analytics_summary(days: int = 7) -> str:
    """Get recent analytics snapshots to understand performance trends.
    Args:
        days: Number of days of history to retrieve.
    """
    db = get_db()
    rows = db.execute(
        "SELECT * FROM analytics_snapshots ORDER BY snapshot_date DESC LIMIT ?",
        (days,),
    ).fetchall()
    if not rows:
        return "No analytics data yet."
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


@tool
def db_save_analytics_snapshot(
    follower_count: int,
    total_posts: int,
    avg_engagement_rate: float,
    total_impressions: int,
    total_reach: int,
    top_post_id: str,
    top_post_engagement: int,
    insights_json: str,
    recommendations: str,
) -> str:
    """Save a daily analytics snapshot.
    Args:
        follower_count: Current follower count.
        total_posts: Total number of posts.
        avg_engagement_rate: Average engagement rate as a decimal.
        total_impressions: Total impressions in the period.
        total_reach: Total reach in the period.
        top_post_id: Instagram media ID of the top performing post.
        top_post_engagement: Engagement count of the top post.
        insights_json: Full insights data as JSON string.
        recommendations: Agent-generated strategy recommendations.
    """
    db = get_db()
    db.execute(
        """INSERT INTO analytics_snapshots
           (follower_count, total_posts, avg_engagement_rate, total_impressions, total_reach,
            top_post_id, top_post_engagement, insights_json, recommendations)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (follower_count, total_posts, avg_engagement_rate, total_impressions, total_reach,
         top_post_id, top_post_engagement, insights_json, recommendations),
    )
    db.commit()
    return "Analytics snapshot saved."


@tool
def db_save_post_performance(
    instagram_media_id: str,
    impressions: int,
    reach: int,
    engagement: int,
    likes: int,
    comments: int,
    saves: int,
    caption_snippet: str,
    content_queue_id: int = 0,
) -> str:
    """Save performance metrics for a specific post.
    Args:
        instagram_media_id: The Instagram media ID.
        impressions: Number of impressions.
        reach: Number of unique accounts reached.
        engagement: Total engagement count.
        likes: Number of likes.
        comments: Number of comments.
        saves: Number of saves.
        caption_snippet: First ~50 chars of the caption for identification.
        content_queue_id: Linked content queue item ID (optional, 0 if unknown).
    """
    db = get_db()
    db.execute(
        """INSERT INTO post_performance
           (instagram_media_id, content_queue_id, impressions, reach, engagement, likes, comments, saves, caption_snippet)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (instagram_media_id, content_queue_id or None, impressions, reach, engagement, likes, comments, saves, caption_snippet),
    )
    db.commit()
    return f"Performance saved for post {instagram_media_id}."


@tool
def db_get_post_performance(limit: int = 10) -> str:
    """Get recent post performance data.
    Args:
        limit: Number of records to return.
    """
    db = get_db()
    rows = db.execute(
        "SELECT * FROM post_performance ORDER BY measured_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    if not rows:
        return "No post performance data yet."
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


# ── Engagement Tools ─────────────────────────────────────────────────

@tool
def db_add_engagement_task(
    target_handle: str,
    action_type: str,
    reason: str,
    suggested_comment: str = "",
    target_post_url: str = "",
) -> str:
    """Add a suggested engagement action for human review.
    Args:
        target_handle: Instagram handle to engage with.
        action_type: Type of action: 'comment', 'like', 'follow', 'dm'.
        reason: Why this engagement matters for the business.
        suggested_comment: Draft comment text (for comment actions).
        target_post_url: URL of the specific post to engage with (optional).
    """
    db = get_db()
    db.execute(
        """INSERT INTO engagement_tasks (target_handle, action_type, reason, suggested_comment, target_post_url)
           VALUES (?, ?, ?, ?, ?)""",
        (target_handle, action_type, reason, suggested_comment, target_post_url),
    )
    db.commit()
    return f"Engagement task added: {action_type} on @{target_handle}."


@tool
def db_get_engagement_tasks(status: str = "pending", limit: int = 20) -> str:
    """Get engagement tasks.
    Args:
        status: Filter by status: 'pending', 'approved', 'done', 'skipped'. Empty for all.
        limit: Max tasks to return.
    """
    db = get_db()
    if status:
        rows = db.execute(
            "SELECT * FROM engagement_tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
            (status, limit),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM engagement_tasks ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    if not rows:
        return f"No engagement tasks{' with status=' + status if status else ''}."
    return json.dumps([dict(r) for r in rows], indent=2, default=str)


# ── Run Log Tools ────────────────────────────────────────────────────

@tool
def db_log_run(task_type: str, status: str, duration_seconds: float, summary: str, error: str = "") -> str:
    """Log a scheduler run for tracking.
    Args:
        task_type: The task that was run.
        status: 'completed' or 'failed'.
        duration_seconds: How long the run took.
        summary: What happened.
        error: Error message if failed.
    """
    db = get_db()
    db.execute(
        "INSERT INTO run_log (task_type, status, duration_seconds, summary, error) VALUES (?, ?, ?, ?, ?)",
        (task_type, status, duration_seconds, summary, error),
    )
    db.commit()
    return f"Run logged: {task_type} - {status}."
