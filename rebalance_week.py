"""One-off: rebalance the current (and next) week's content so each day has
at most one photo and at most one story.

Set DATABASE_URL in the environment to hit Railway; omit it to hit the local
SQLite DB. Pass --brand to target a specific brand (default: mila), and
--dry-run to see moves without writing.

Usage::

    python rebalance_week.py                  # local sqlite, brand=mila, apply
    python rebalance_week.py --dry-run        # preview only
    BRAND=mila python rebalance_week.py       # env var form
    DATABASE_URL=postgres://... python rebalance_week.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

from dotenv import load_dotenv

load_dotenv()

from brands.loader import init_brand
from db.connection import get_db
from tools.schedule_normalizer import compute_reschedule, current_week_start


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand", default=os.environ.get("BRAND", "mila"),
                        help="Brand slug (default: mila)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print moves without writing")
    parser.add_argument("--weeks", type=int, default=2,
                        help="How many consecutive weeks to normalize (default: 2)")
    args = parser.parse_args()

    bc = init_brand(args.brand)
    photo_time = (bc.content_strategy.feed_post_times or ["07:00"])[0]
    story_time = bc.content_strategy.story_time or "09:00"
    photo_days = bc.content_strategy.weekly_feed_posts or 5
    story_days = bc.content_strategy.weekly_stories or 7

    db = get_db()
    today = date.today()

    total_moves = 0
    for w in range(args.weeks):
        week_start = current_week_start(today) + timedelta(weeks=w)
        week_end = week_start + timedelta(days=6)

        rows = db.execute(
            "SELECT id, content_type, scheduled_date, scheduled_time, status, topic "
            "FROM content_queue "
            "WHERE brand_id = ? AND scheduled_date >= ? AND scheduled_date <= ? "
            "ORDER BY scheduled_date, scheduled_time, id",
            (bc.slug, week_start.isoformat(), week_end.isoformat()),
        ).fetchall()
        posts = [dict(r) for r in rows]

        print(f"\n=== Week {week_start.isoformat()} ({len(posts)} posts) ===")
        if not posts:
            continue

        moves = compute_reschedule(
            posts,
            week_start,
            photo_time=photo_time,
            story_time=story_time,
            photo_days=photo_days,
            story_days=story_days,
        )

        if not moves:
            print("  already balanced")
            continue

        posts_by_id = {p["id"]: p for p in posts}
        for post_id, new_date, new_time in moves:
            p = posts_by_id[post_id]
            old = f'{p.get("scheduled_date") or "—"} {str(p.get("scheduled_time") or "—")[:5]}'
            new = f'{new_date or "unscheduled"} {new_time or ""}'.strip()
            topic = (p.get("topic") or "")[:40]
            print(f"  #{post_id} [{p['content_type']}] {old}  →  {new}   {topic}")

        total_moves += len(moves)

        if not args.dry_run:
            for post_id, new_date, new_time in moves:
                db.execute(
                    "UPDATE content_queue SET scheduled_date = ?, scheduled_time = ? "
                    "WHERE id = ? AND brand_id = ?",
                    (new_date, new_time, post_id, bc.slug),
                )
            db.commit()

    print(
        f"\n{'Would apply' if args.dry_run else 'Applied'} {total_moves} move(s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
