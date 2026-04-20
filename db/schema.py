import threading

from db.connection import get_db, _is_postgres

# Use SERIAL for Postgres, INTEGER PRIMARY KEY AUTOINCREMENT for SQLite
_PK = "SERIAL PRIMARY KEY" if _is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
_DATE_DEFAULT = "CURRENT_DATE" if _is_postgres() else "(date('now'))"

_init_done = False
_init_lock = threading.Lock()


def _tables():
    return [
        f"""
        CREATE TABLE IF NOT EXISTS content_queue (
            id {_PK},
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
        f"""
        CREATE TABLE IF NOT EXISTS leads (
            id {_PK},
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
        f"""
        CREATE TABLE IF NOT EXISTS analytics_snapshots (
            id {_PK},
            snapshot_date DATE DEFAULT {_DATE_DEFAULT},
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
        f"""
        CREATE TABLE IF NOT EXISTS post_performance (
            id {_PK},
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
        f"""
        CREATE TABLE IF NOT EXISTS engagement_tasks (
            id {_PK},
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
        f"""
        CREATE TABLE IF NOT EXISTS run_log (
            id {_PK},
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            task_type TEXT,
            status TEXT,
            duration_seconds REAL,
            summary TEXT,
            error TEXT
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS brand_credentials (
            id {_PK},
            brand_id TEXT NOT NULL,
            credential_key TEXT NOT NULL,
            credential_value TEXT,
            expires_at TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (brand_id, credential_key)
        )
        """,
    ]


def _add_column_if_missing(db, table, column, col_type="TEXT"):
    """Add a column to a table if it doesn't exist. Works for both SQLite and Postgres."""
    if _is_postgres():
        db.execute(
            f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {col_type}"
        )
    else:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
        except Exception:
            pass


def init_db():
    global _init_done
    with _init_lock:
        if _init_done:
            return
        db = get_db()
        for table_sql in _tables():
            db.execute(table_sql)
        _add_column_if_missing(db, "content_queue", "image_url_alt")
        _add_column_if_missing(db, "content_queue", "retry_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(db, "run_log", "error_category")

        # Multi-brand support: add brand_id to all tables
        for table in ("content_queue", "leads", "analytics_snapshots",
                      "post_performance", "engagement_tasks", "run_log"):
            _add_column_if_missing(db, table, "brand_id", "TEXT DEFAULT 'capa-co'")

        db.commit()
        _init_done = True
        print("Database initialized successfully.")


if __name__ == "__main__":
    init_db()
