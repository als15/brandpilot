"""Sunday planning session — run the full weekly content pipeline and send to Telegram."""

import asyncio
from dotenv import load_dotenv
load_dotenv()

from brands.loader import init_brand
from db.schema import init_db
from db.connection import get_db
from graph.orchestrator import run_task
from telegram_bot import build_telegram_app
from daemon import _send_pending_approvals


async def main():
    bc = init_brand()
    init_db()
    db = get_db()
    slug = bc.slug

    # Check if content already exists for this week
    existing = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue WHERE status != 'published' AND status != 'rejected'"
    ).fetchone()["cnt"]

    if existing > 0:
        print(f"\n=== {existing} posts already in queue — skipping content planning ===")
    else:
        # Step 1: Content planning
        print("\n=== Step 1: Content Planning ===")
        summary = run_task("content_planning", slug)
        print(summary[:300])

        # Step 2: Design review
        print("\n=== Step 2: Design Review ===")
        summary = run_task("design_review", slug)
        print(summary[:300])

    # Step 3: Image generation (only for drafts without images)
    drafts = db.execute(
        "SELECT COUNT(*) as cnt FROM content_queue "
        "WHERE status = 'draft' AND visual_direction IS NOT NULL AND image_url IS NULL"
    ).fetchone()["cnt"]

    if drafts > 0:
        print(f"\n=== Step 3: Generating images for {drafts} posts ===")
        summary = run_task("image_generation", slug)
        print(summary[:300])
    else:
        print("\n=== No drafts need images — skipping image generation ===")

    # Step 4: Send Telegram notifications
    print("\n=== Step 4: Sending to Telegram ===")
    import os
    app = build_telegram_app(os.environ.get("TELEGRAM_BOT_TOKEN", ""))
    async with app:
        await _send_pending_approvals(app.bot, slug)
    print("Done! Check Telegram for your weekly content review.")


if __name__ == "__main__":
    asyncio.run(main())
