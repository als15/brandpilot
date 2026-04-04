from db.connection import get_db

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS content_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        scheduled_date TEXT,
        scheduled_time TEXT,
        content_type TEXT,
        content_pillar TEXT,
        topic TEXT,
        caption TEXT,
        hashtags TEXT,
        visual_direction TEXT,
        image_url TEXT,
        image_url_alt TEXT,
        status TEXT DEFAULT 'draft',
        approved_by TEXT,
        approved_at TIMESTAMP,
        published_at TIMESTAMP,
        instagram_media_id TEXT,
        notes TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        business_name TEXT,
        instagram_handle TEXT,
        business_type TEXT,
        location TEXT,
        source TEXT,
        follower_count INTEGER,
        status TEXT DEFAULT 'discovered',
        outreach_message TEXT,
        notes TEXT,
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS analytics_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        snapshot_date DATE DEFAULT (date('now')),
        follower_count INTEGER,
        total_posts INTEGER,
        avg_engagement_rate REAL,
        total_impressions INTEGER,
        total_reach INTEGER,
        top_post_id TEXT,
        top_post_engagement INTEGER,
        insights_json TEXT,
        recommendations TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS post_performance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        instagram_media_id TEXT,
        content_queue_id INTEGER REFERENCES content_queue(id),
        measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        impressions INTEGER,
        reach INTEGER,
        engagement INTEGER,
        likes INTEGER,
        comments INTEGER,
        saves INTEGER,
        caption_snippet TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS engagement_tasks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        target_handle TEXT,
        target_post_url TEXT,
        action_type TEXT,
        suggested_comment TEXT,
        reason TEXT,
        status TEXT DEFAULT 'pending',
        completed_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        task_type TEXT,
        status TEXT,
        duration_seconds REAL,
        summary TEXT,
        error TEXT
    )
    """,
]


MIGRATIONS = [
    "ALTER TABLE content_queue ADD COLUMN image_url_alt TEXT",
]


def init_db():
    db = get_db()
    for table_sql in TABLES:
        db.execute(table_sql)
    for migration in MIGRATIONS:
        try:
            db.execute(migration)
        except Exception:
            pass  # column already exists
    db.commit()
    print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
