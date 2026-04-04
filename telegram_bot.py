"""Telegram bot for Capa & Co Instagram agent notifications and approvals."""

import os
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from db.connection import get_db

log = logging.getLogger("capaco")


def _authorized(update: Update) -> bool:
    """Check if the user is authorized."""
    allowed = os.environ.get("TELEGRAM_AUTHORIZED_USERS", "")
    if not allowed:
        return True
    allowed_ids = {int(uid.strip()) for uid in allowed.split(",") if uid.strip()}
    return update.effective_user.id in allowed_ids


def _chat_id() -> str:
    return os.environ["TELEGRAM_CHAT_ID"]


# ── Command Handlers ─────────────────────────────────────────────────


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    await update.message.reply_text(
        "Capa & Co Instagram Bot\n\n"
        "Commands:\n"
        "/status - Account stats & recent runs\n"
        "/queue - Content queue overview\n"
        "/leads - Recent leads\n"
        "/engage - Pending engagement tasks\n"
        f"\nYour Telegram ID: {update.effective_user.id}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    db = get_db()

    # Queue stats
    counts = db.execute(
        "SELECT status, COUNT(*) as cnt FROM content_queue GROUP BY status"
    ).fetchall()
    queue_lines = [f"  {r['status']}: {r['cnt']}" for r in counts] or ["  (empty)"]

    # Recent runs
    runs = db.execute(
        "SELECT task_type, status, started_at FROM run_log ORDER BY started_at DESC LIMIT 5"
    ).fetchall()
    run_lines = [
        f"  {r['started_at'][:16]} | {r['task_type']} | {r['status']}" for r in runs
    ] or ["  (none)"]

    text = (
        "Content Queue:\n" + "\n".join(queue_lines) +
        "\n\nRecent Runs:\n" + "\n".join(run_lines)
    )
    await update.message.reply_text(text)


async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    db = get_db()
    rows = db.execute(
        "SELECT id, scheduled_date, topic, status, image_url "
        "FROM content_queue WHERE status NOT IN ('published', 'rejected') "
        "ORDER BY scheduled_date LIMIT 15"
    ).fetchall()

    if not rows:
        await update.message.reply_text("Content queue is empty.")
        return

    lines = []
    for r in rows:
        img = "img" if r["image_url"] else "no-img"
        lines.append(f"[{r['id']}] {r['scheduled_date'] or '?'} | {r['status']} | {img} | {r['topic'][:35]}")

    await update.message.reply_text("Content Queue:\n" + "\n".join(lines))


async def leads_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    db = get_db()
    rows = db.execute(
        "SELECT business_name, business_type, status FROM leads ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    if not rows:
        await update.message.reply_text("No leads yet.")
        return

    lines = [f"  {r['business_name']} ({r['business_type']}) - {r['status']}" for r in rows]
    await update.message.reply_text("Recent Leads:\n" + "\n".join(lines))


async def engage_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _authorized(update):
        return
    db = get_db()
    rows = db.execute(
        "SELECT target_handle, action_type, suggested_comment FROM engagement_tasks "
        "WHERE status = 'pending' ORDER BY created_at DESC LIMIT 10"
    ).fetchall()

    if not rows:
        await update.message.reply_text("No pending engagement tasks.")
        return

    lines = [
        f"  @{r['target_handle']} - {r['action_type']}: {r['suggested_comment'][:50] if r['suggested_comment'] else '(no comment)'}"
        for r in rows
    ]
    await update.message.reply_text("Pending Engagement:\n" + "\n".join(lines))


# ── Approval Callback ────────────────────────────────────────────────


async def approval_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle approve/reject button presses."""
    query = update.callback_query
    await query.answer()

    if not _authorized(update):
        await query.edit_message_caption(caption="Unauthorized.")
        return

    # Callback formats:
    #   pick_{post_id}_{model}   — upscale picked image, approve
    #   regen_{post_id}_{model}  — regenerate a new image for that model
    #   reject_{post_id}         — reject post
    #   approve_{post_id}        — legacy single-image approve
    data = query.data
    db = get_db()

    if data.startswith("reject_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute("SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return
        db.execute("UPDATE content_queue SET status = 'rejected' WHERE id = ?", (post_id,))
        db.commit()
        await query.edit_message_caption(caption=f"REJECTED: {row['topic']}")
        log.info(f"Post {post_id} rejected via Telegram.")
        return

    if data.startswith("pick_"):
        _, post_id_str, model_short = data.split("_", 2)
        post_id = int(post_id_str)
        model_full = "flux-2-pro" if model_short == "flux" else "nano-banana-2"

        row = db.execute(
            "SELECT topic, status, image_url, image_url_alt, visual_direction "
            "FROM content_queue WHERE id = ?",
            (post_id,),
        ).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        # Pick the URL for the chosen model
        picked_url = row["image_url"] if model_short == "flux" else row["image_url_alt"]

        await query.edit_message_caption(caption=f"Upscaling {model_full}...")
        log.info(f"Post {post_id} — upscaling {model_full}...")

        from tools.image_gen import upscale_and_host
        final_url = upscale_and_host(picked_url)

        db.execute(
            "UPDATE content_queue SET status = 'approved', approved_by = 'telegram', "
            "approved_at = CURRENT_TIMESTAMP, image_url = ?, image_url_alt = NULL WHERE id = ?",
            (final_url, post_id),
        )
        db.commit()
        await query.edit_message_caption(
            caption=f"APPROVED ({model_full}): {row['topic']}\n\nUpscaled and ready to publish."
        )
        log.info(f"Post {post_id} — picked {model_full}, upscaled, approved.")
        return

    if data.startswith("editcap_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute("SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        # Store post_id in user_data so the next text message updates the caption
        context.user_data["editing_caption_for"] = post_id
        await query.edit_message_caption(
            caption=f"Editing caption for: {row['topic']}\n\nSend your new caption as a message."
        )
        log.info(f"Post {post_id} — caption edit mode activated.")
        return

    if data.startswith("regen_"):
        _, post_id_str, model_short = data.split("_", 2)
        post_id = int(post_id_str)
        model_full = "flux-2-pro" if model_short == "flux" else "nano-banana-2"

        row = db.execute(
            "SELECT topic, status, visual_direction FROM content_queue WHERE id = ?",
            (post_id,),
        ).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        await query.edit_message_caption(caption=f"Regenerating {model_full}...")
        log.info(f"Post {post_id} — regenerating {model_full}...")

        from tools.content_guide import build_image_prompt
        from tools.image_gen import generate_one

        prompt = build_image_prompt.invoke(row["visual_direction"])
        new_url = generate_one(prompt, model_full)

        # Update the appropriate URL column
        col = "image_url" if model_short == "flux" else "image_url_alt"
        db.execute(f"UPDATE content_queue SET {col} = ? WHERE id = ?", (new_url, post_id))
        db.commit()

        # Send new image with full buttons again
        bot = context.bot
        other_col = "image_url_alt" if model_short == "flux" else "image_url"
        other_row = db.execute(
            f"SELECT {other_col}, topic, caption FROM content_queue WHERE id = ?",
            (post_id,),
        ).fetchone()

        await bot.send_photo(
            chat_id=query.message.chat_id,
            photo=new_url,
            caption=f"NEW {model_full} for: {row['topic']}",
            reply_markup=_build_comparison_keyboard(post_id),
        )
        log.info(f"Post {post_id} — regenerated {model_full}: {new_url}")
        return

    # Legacy: approve_{post_id}
    action, post_id_str = data.split("_", 1)
    post_id = int(post_id_str)
    row = db.execute("SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)).fetchone()
    if not row:
        await query.edit_message_caption(caption=f"Post {post_id} not found.")
        return
    if row["status"] != "pending_approval":
        await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
        return
    db.execute(
        "UPDATE content_queue SET status = 'approved', approved_by = 'telegram', "
        "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (post_id,),
    )
    db.commit()
    await query.edit_message_caption(
        caption=f"APPROVED: {row['topic']}\n\nWill be published on next publish run."
    )
    log.info(f"Post {post_id} approved via Telegram.")


# ── Caption Edit Handler ─────────────────────────────────────────────


async def caption_edit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive a new caption from the user after they pressed Edit Caption."""
    if not _authorized(update):
        return

    post_id = context.user_data.pop("editing_caption_for", None)
    if post_id is None:
        return  # Not in edit mode, ignore

    new_caption = update.message.text.strip()
    db = get_db()
    row = db.execute(
        "SELECT topic, status, image_url, image_url_alt FROM content_queue WHERE id = ?",
        (post_id,),
    ).fetchone()

    if not row or row["status"] != "pending_approval":
        await update.message.reply_text(f"Post {post_id} is no longer pending approval.")
        return

    db.execute("UPDATE content_queue SET caption = ? WHERE id = ?", (new_caption, post_id))
    db.commit()

    # Re-send the review with updated caption and images
    review_text = (
        f"📋 POST #{post_id} — CAPTION UPDATED\n\n"
        f"Topic: {row['topic']}\n\n"
        f"━━━ New Caption ━━━\n"
        f"{new_caption}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(review_text[:4096])

    if row["image_url_alt"]:
        keyboard = _build_comparison_keyboard(post_id)
        await context.bot.send_photo(
            chat_id=update.message.chat_id, photo=row["image_url"], caption="A — flux-2-pro",
        )
        await context.bot.send_photo(
            chat_id=update.message.chat_id, photo=row["image_url_alt"],
            caption="B — nano-banana-2", reply_markup=keyboard,
        )
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"approve_{post_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject_{post_id}"),
            ]
        ])
        await context.bot.send_photo(
            chat_id=update.message.chat_id, photo=row["image_url"],
            caption="Image", reply_markup=keyboard,
        )
    log.info(f"Post {post_id} — caption updated via Telegram.")


# ── Notification Senders (called by daemon) ──────────────────────────


def _build_comparison_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Build the standard pick/regen/reject/edit keyboard for A/B comparison."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Pick A (flux-2-pro)", callback_data=f"pick_{post_id}_flux"),
            InlineKeyboardButton("Pick B (nano-banana-2)", callback_data=f"pick_{post_id}_banana"),
        ],
        [
            InlineKeyboardButton("Regen A", callback_data=f"regen_{post_id}_flux"),
            InlineKeyboardButton("Regen B", callback_data=f"regen_{post_id}_banana"),
        ],
        [
            InlineKeyboardButton("Edit Caption", callback_data=f"editcap_{post_id}"),
            InlineKeyboardButton("Reject Both", callback_data=f"reject_{post_id}"),
        ],
    ])


async def notify_pending_approval(
    bot: Bot, post_id: int, topic: str, caption: str,
    image_url: str, image_url_alt: str | None = None,
):
    """Send preview images with full caption and action buttons to Telegram."""
    review_text = (
        f"📋 POST #{post_id} FOR REVIEW\n\n"
        f"Topic: {topic}\n\n"
        f"━━━ Caption (will be published) ━━━\n"
        f"{caption}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    if image_url_alt:
        keyboard = _build_comparison_keyboard(post_id)
        try:
            await bot.send_message(chat_id=_chat_id(), text=review_text[:4096])
            await bot.send_photo(chat_id=_chat_id(), photo=image_url, caption="A — flux-2-pro")
            await bot.send_photo(
                chat_id=_chat_id(), photo=image_url_alt,
                caption="B — nano-banana-2", reply_markup=keyboard,
            )
        except Exception as e:
            await bot.send_message(
                chat_id=_chat_id(),
                text=f"{review_text}\n\nA: {image_url}\nB: {image_url_alt}",
                reply_markup=keyboard,
            )
            log.warning(f"Failed to send images for post {post_id}: {e}")
    else:
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Approve", callback_data=f"approve_{post_id}"),
                InlineKeyboardButton("Reject", callback_data=f"reject_{post_id}"),
            ]
        ])
        try:
            await bot.send_photo(
                chat_id=_chat_id(), photo=image_url,
                caption=review_text[:1024],
                reply_markup=keyboard,
            )
        except Exception as e:
            await bot.send_message(
                chat_id=_chat_id(),
                text=f"{review_text}\n\nImage: {image_url}",
                reply_markup=keyboard,
            )
            log.warning(f"Failed to send image for post {post_id}: {e}")


async def notify_task_complete(bot: Bot, task_type: str, summary: str):
    """Notify that a scheduled task completed."""
    text = f"Task completed: {task_type}\n\n{summary[:500]}"
    await bot.send_message(chat_id=_chat_id(), text=text)


async def notify_error(bot: Bot, task_type: str, error_msg: str):
    """Notify that a scheduled task failed."""
    text = f"TASK FAILED: {task_type}\n\nError: {error_msg[:500]}"
    await bot.send_message(chat_id=_chat_id(), text=text)


# ── Builder ──────────────────────────────────────────────────────────


def build_telegram_app() -> Application:
    """Build the Telegram Application with all handlers."""
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(CommandHandler("engage", engage_command))
    app.add_handler(CallbackQueryHandler(approval_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, caption_edit_handler))

    return app
