"""Telegram bot for multi-brand Instagram agent notifications and approvals."""

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

import brands.loader as _brand_loader
from db.connection import get_db

log = logging.getLogger("capaco")


def _activate_brand_for_handler(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch the global brand_config to the brand this Application belongs to.

    Each Telegram Application is tagged with ``bot_data["brand_slug"]`` in
    daemon.py, so a handler can resolve which brand's data, credentials, and
    voice it should use regardless of which bot received the update.
    """
    slug = context.application.bot_data.get("brand_slug")
    if slug and _brand_loader.brand_config.slug != slug:
        _brand_loader.set_brand(slug)


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
    _activate_brand_for_handler(context)
    if not _authorized(update):
        await update.message.reply_text("Unauthorized.")
        return
    brand_name = _brand_loader.brand_config.identity.name_en or _brand_loader.brand_config.slug
    await update.message.reply_text(
        f"{brand_name} Instagram Bot\n\n"
        "Commands:\n"
        "/status - Account stats & recent runs\n"
        "/queue - Content queue overview\n"
        "/leads - Recent leads\n"
        "/engage - Pending engagement tasks\n"
        "/health - System health check\n"
        f"\nYour Telegram ID: {update.effective_user.id}"
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _activate_brand_for_handler(context)
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
    _activate_brand_for_handler(context)
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
    _activate_brand_for_handler(context)
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
    _activate_brand_for_handler(context)
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
    _activate_brand_for_handler(context)
    query = update.callback_query
    await query.answer()

    if not _authorized(update):
        await query.edit_message_caption(caption="Unauthorized.")
        return

    # Callback formats:
    #   approve_{post_id}  — upscale image, approve
    #   regen_{post_id}    — regenerate image
    #   editcap_{post_id}  — edit caption
    #   reject_{post_id}   — reject post
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

    if data.startswith("republish_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute(
            "SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)
        ).fetchone()
        if not row:
            await query.edit_message_text(text=f"Post {post_id} not found.")
            return
        if row["status"] != "failed":
            await query.edit_message_text(
                text=f"Post {post_id} is '{row['status']}', not failed — nothing to republish."
            )
            return
        db.execute(
            "UPDATE content_queue SET status = 'approved', retry_count = 0 WHERE id = ?",
            (post_id,),
        )
        db.commit()
        await query.edit_message_text(
            text=f"RE-QUEUED: {row['topic']}\nPost #{post_id}\nWill publish on the next scheduled run."
        )
        log.info(f"Post {post_id} re-queued for publish via Telegram.")
        return

    if data.startswith("publishnow_"):
        import asyncio as _asyncio
        from agents.content_publisher import publish_one

        post_id = int(data.split("_", 1)[1])
        row = db.execute(
            "SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)
        ).fetchone()
        if not row:
            await query.edit_message_text(text=f"Post {post_id} not found.")
            return
        if row["status"] not in ("approved", "failed"):
            await query.edit_message_text(
                text=f"Post {post_id} is '{row['status']}', cannot publish."
            )
            return

        await query.edit_message_text(
            text=f"Publishing #{post_id} now... ({row['topic']})"
        )
        # publish_one hits the IG API — run in a thread so we don't block the
        # event loop (which also serves other button presses).
        slug = _brand_loader.brand_config.slug
        ok, msg = await _asyncio.to_thread(publish_one, post_id, slug)
        header = "PUBLISHED" if ok else "PUBLISH FAILED"
        await query.edit_message_text(
            text=f"{header}: {row['topic']}\nPost #{post_id}\n{msg}"
        )
        log.info(f"Post {post_id} publish_now via Telegram: ok={ok} msg={msg}")
        return

    if data.startswith("approve_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute(
            "SELECT topic, status, image_url FROM content_queue WHERE id = ?",
            (post_id,),
        ).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        # Approve first so the post isn't stuck in pending_approval if upscale fails
        db.execute(
            "UPDATE content_queue SET status = 'approved', approved_by = 'telegram', "
            "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (post_id,),
        )
        db.commit()
        log.info(f"Post {post_id} — approved, starting upscale...")

        await query.edit_message_caption(caption=f"Approved! Upscaling image...")

        try:
            from tools.image_gen import upscale_and_host
            final_url = upscale_and_host(row["image_url"])
            db.execute(
                "UPDATE content_queue SET image_url = ? WHERE id = ?",
                (final_url, post_id),
            )
            db.commit()
            await query.edit_message_caption(
                caption=f"APPROVED: {row['topic']}\n\nUpscaled and ready to publish."
            )
            log.info(f"Post {post_id} — upscaled successfully.")
        except Exception as e:
            log.error(f"Post {post_id} — upscale failed: {e}, will publish with original image.")
            await query.edit_message_caption(
                caption=f"APPROVED: {row['topic']}\n\n⚠ Upscale failed, will publish with original image."
            )
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

        context.user_data["editing_caption_for"] = post_id
        await query.edit_message_caption(
            caption=f"Editing caption for: {row['topic']}\n\nSend your new caption as a message."
        )
        log.info(f"Post {post_id} — caption edit mode activated.")
        return

    if data.startswith("regen_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute(
            "SELECT topic, status, visual_direction, content_type FROM content_queue WHERE id = ?",
            (post_id,),
        ).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        await query.edit_message_caption(caption=f"Regenerating...")
        log.info(f"Post {post_id} — regenerating...")

        from tools.content_guide import build_image_prompt
        from tools.image_gen import generate_one

        prompt = build_image_prompt.invoke(row["visual_direction"])
        new_url = generate_one(prompt, content_type=row["content_type"] or "photo")

        db.execute("UPDATE content_queue SET image_url = ? WHERE id = ?", (new_url, post_id))
        db.commit()

        await context.bot.send_photo(
            chat_id=query.message.chat_id,
            photo=new_url,
            caption=f"NEW image for: {row['topic']}",
            reply_markup=_build_review_keyboard(post_id),
        )
        log.info(f"Post {post_id} — regenerated: {new_url}")
        return

    if data.startswith("directregen_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute("SELECT topic, status FROM content_queue WHERE id = ?", (post_id,)).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return

        context.user_data["directing_regen_for"] = post_id
        await query.edit_message_caption(
            caption=(
                f"Directing regen for: {row['topic']}\n\n"
                "Send your direction as a message, e.g.:\n"
                "  tiramisu close up\n"
                "  babka with chocolate drizzle, overhead shot\n\n"
                "To add a reference image, paste a URL on the next line:\n"
                "  challah braiding, warm light\n"
                "  https://example.com/reference.jpg\n\n"
                "I'll generate the image AND write a matching caption."
            )
        )
        log.info(f"Post {post_id} — directed regen mode activated.")
        return

    if data.startswith("refine_"):
        post_id = int(data.split("_", 1)[1])
        row = db.execute(
            "SELECT topic, status, image_url FROM content_queue WHERE id = ?", (post_id,),
        ).fetchone()
        if not row:
            await query.edit_message_caption(caption=f"Post {post_id} not found.")
            return
        if row["status"] != "pending_approval":
            await query.edit_message_caption(caption=f"Post {post_id} is already '{row['status']}'.")
            return
        if not row["image_url"]:
            await query.edit_message_caption(caption=f"Post {post_id} has no image to refine yet.")
            return

        context.user_data["refining_for"] = post_id
        await query.edit_message_caption(
            caption=(
                f"Refining image for: {row['topic']}\n\n"
                "Send the change you want as a message, e.g.:\n"
                "  add my logo in the middle\n"
                "  make the background warmer\n"
                "  add vanilla beans on top\n\n"
                "The current image is used as the reference — each refine "
                "builds on the previous one."
            )
        )
        log.info(f"Post {post_id} — refine mode activated.")
        return


# ── Caption Edit Handler ─────────────────────────────────────────────


async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages for caption edits and directed regens."""
    _activate_brand_for_handler(context)
    if not _authorized(update):
        return

    # Check if user is in caption edit mode
    post_id = context.user_data.pop("editing_caption_for", None)
    if post_id is not None:
        await _handle_caption_edit(update, context, post_id)
        return

    # Check if user is in directed regen mode
    post_id = context.user_data.pop("directing_regen_for", None)
    if post_id is not None:
        await _handle_directed_regen(update, context, post_id)
        return

    # Check if user is in refine mode
    post_id = context.user_data.pop("refining_for", None)
    if post_id is not None:
        await _handle_refine(update, context, post_id)
        return

    # Not in any mode, ignore


async def _handle_caption_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Process a caption edit message."""
    new_caption = update.message.text.strip()
    db = get_db()
    row = db.execute(
        "SELECT topic, status, image_url FROM content_queue WHERE id = ?",
        (post_id,),
    ).fetchone()

    if not row or row["status"] != "pending_approval":
        await update.message.reply_text(f"Post {post_id} is no longer pending approval.")
        return

    db.execute("UPDATE content_queue SET caption = ? WHERE id = ?", (new_caption, post_id))
    db.commit()

    review_text = (
        f"POST #{post_id} — CAPTION UPDATED\n\n"
        f"Topic: {row['topic']}\n\n"
        f"--- New Caption ---\n"
        f"{new_caption}\n"
        f"-------------------"
    )

    await context.bot.send_photo(
        chat_id=update.message.chat_id,
        photo=row["image_url"],
        caption=review_text[:1024],
        reply_markup=_build_review_keyboard(post_id),
    )
    log.info(f"Post {post_id} — caption updated via Telegram.")


async def _handle_directed_regen(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Process a directed regeneration: generate image from user's direction + LLM caption."""
    raw_text = update.message.text.strip()

    # Parse optional reference image URL (any line starting with http:// or https://)
    reference_url = None
    direction_lines = []
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith(("http://", "https://")) and not reference_url:
            reference_url = stripped
        else:
            direction_lines.append(stripped)
    user_direction = " ".join(direction_lines).strip()

    if not user_direction:
        await update.message.reply_text("Please include a direction, not just a URL.")
        return

    db = get_db()
    row = db.execute(
        "SELECT topic, status, content_pillar, content_type FROM content_queue WHERE id = ?",
        (post_id,),
    ).fetchone()

    if not row or row["status"] != "pending_approval":
        await update.message.reply_text(f"Post {post_id} is no longer pending approval.")
        return

    ref_note = f"\n(with reference image)" if reference_url else ""
    await update.message.reply_text(f"Generating image for: {user_direction}{ref_note}\nThis may take a moment...")
    log.info(f"Post {post_id} — directed regen: '{user_direction}' ref={reference_url}")

    # Generate image from user direction
    from tools.content_guide import build_image_prompt, build_reference_edit_prompt
    from tools.image_gen import generate_one

    # With a reference image, use a minimal prompt so the reference drives the
    # visual style. Wrapping with the full brand template would force the
    # reference to be overridden and produce off-target results.
    if reference_url:
        prompt = build_reference_edit_prompt(user_direction)
    else:
        prompt = build_image_prompt.invoke(user_direction)
    new_url = generate_one(prompt, reference_url=reference_url, content_type=row["content_type"] or "photo")

    # Generate matching caption via LLM
    new_caption = _generate_caption(user_direction, row["content_pillar"])

    # Update DB: image, caption, visual_direction, and topic
    db.execute(
        "UPDATE content_queue SET image_url = ?, caption = ?, visual_direction = ?, topic = ? WHERE id = ?",
        (new_url, new_caption, user_direction, user_direction, post_id),
    )
    db.commit()

    review_text = (
        f"POST #{post_id} — DIRECTED REGEN\n\n"
        f"Direction: {user_direction}\n\n"
        f"--- Generated Caption ---\n"
        f"{new_caption}\n"
        f"-------------------------"
    )

    await context.bot.send_photo(
        chat_id=update.message.chat_id,
        photo=new_url,
        caption=review_text[:1024],
        reply_markup=_build_review_keyboard(post_id),
    )
    log.info(f"Post {post_id} — directed regen complete: {new_url}")


async def _handle_refine(update: Update, context: ContextTypes.DEFAULT_TYPE, post_id: int):
    """Process an iterative refine: use the current image as the reference
    and apply a short edit instruction. Caption is left untouched."""
    direction = update.message.text.strip()
    if not direction:
        await update.message.reply_text("Please describe the change you want.")
        return

    db = get_db()
    row = db.execute(
        "SELECT topic, status, image_url, caption, content_type FROM content_queue WHERE id = ?",
        (post_id,),
    ).fetchone()

    if not row or row["status"] != "pending_approval":
        await update.message.reply_text(f"Post {post_id} is no longer pending approval.")
        return
    if not row["image_url"]:
        await update.message.reply_text(f"Post {post_id} has no image to refine.")
        return

    await update.message.reply_text(f"Refining image: {direction}\nThis may take a moment...")
    log.info(f"Post {post_id} — refine: '{direction}' ref={row['image_url']}")

    from tools.content_guide import build_reference_edit_prompt
    from tools.image_gen import generate_one

    prompt = build_reference_edit_prompt(direction)
    new_url = generate_one(prompt, reference_url=row["image_url"], content_type=row["content_type"] or "photo")

    db.execute("UPDATE content_queue SET image_url = ? WHERE id = ?", (new_url, post_id))
    db.commit()

    review_text = (
        f"POST #{post_id} — REFINED\n\n"
        f"Change: {direction}\n\n"
        f"--- Caption (unchanged) ---\n"
        f"{row['caption']}\n"
        f"---------------------------"
    )

    await context.bot.send_photo(
        chat_id=update.message.chat_id,
        photo=new_url,
        caption=review_text[:1024],
        reply_markup=_build_review_keyboard(post_id),
    )
    log.info(f"Post {post_id} — refine complete: {new_url}")


# ── Caption Generation ──────────────────────────────────────────────


def _generate_caption(direction: str, content_pillar: str) -> str:
    """Generate a caption for a given visual direction using the active brand's voice."""
    from config import get_llm

    bc = _brand_loader.brand_config
    name = bc.identity.name
    name_en = bc.identity.name_en or name
    brand_label = f"{name_en} ({name})" if name and name_en and name != name_en else (name_en or name)
    language = bc.identity.language or "native"
    caption_examples = "\n".join(f"- {ex}" for ex in bc.voice.caption_examples) or "(no examples configured)"
    hashtag_hint = ", ".join(bc.voice.hashtags_default[:5]) if bc.voice.hashtags_default else ""
    tone = bc.voice.tone or ""
    business_type = bc.identity.business_type or ""

    llm = get_llm(temperature=0.7)
    lines = [f"You write Instagram captions for {brand_label}" + (f", {business_type}." if business_type else "."), ""]
    lines.append("Rules:")
    lines.append(f"- Write in native {language}. Not translated. Not corporate.")
    if tone:
        lines.append(f"- Tone: {tone}")
    lines.append("- Short and playful — one line is best, max 2 short sentences.")
    lines.append("- The caption MUST be specifically about the dish/image described below.")
    hashtag_rule = "- Add 3-5 hashtags at the end."
    if hashtag_hint:
        hashtag_rule += f" Use the brand's voice — e.g. {hashtag_hint}."
    lines.append(hashtag_rule)
    lines.append("- Emojis: max one, only if natural.")
    lines.append("")
    lines.append("Examples of good captions:")
    lines.append(caption_examples)
    lines.append("")
    lines.append(f"Content pillar: {content_pillar}")
    lines.append(f"Visual direction: {direction}")
    lines.append("")
    lines.append("Write ONLY the caption. Nothing else.")
    prompt = "\n".join(lines)
    response = llm.invoke(prompt)
    return response.content.strip()


# ── Notification Senders (called by daemon) ──────────────────────────


def _build_review_keyboard(post_id: int) -> InlineKeyboardMarkup:
    """Build the approve/regen/edit/reject keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Approve", callback_data=f"approve_{post_id}"),
            InlineKeyboardButton("Regenerate", callback_data=f"regen_{post_id}"),
        ],
        [
            InlineKeyboardButton("Regen + Direction", callback_data=f"directregen_{post_id}"),
            InlineKeyboardButton("Refine Image", callback_data=f"refine_{post_id}"),
        ],
        [
            InlineKeyboardButton("Edit Caption", callback_data=f"editcap_{post_id}"),
            InlineKeyboardButton("Reject", callback_data=f"reject_{post_id}"),
        ],
    ])


async def notify_pending_approval(
    bot: Bot, post_id: int, topic: str, caption: str, image_url: str, **kwargs,
):
    """Send preview image with full caption and action buttons to Telegram."""
    review_text = (
        f"📋 POST #{post_id} FOR REVIEW\n\n"
        f"Topic: {topic}\n\n"
        f"━━━ Caption (will be published) ━━━\n"
        f"{caption}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    keyboard = _build_review_keyboard(post_id)
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


async def notify_publish_success(bot: Bot, post_id: int, topic: str, image_url: str):
    """Notify that a post was published to Instagram."""
    text = f"Published: {topic}\nPost #{post_id}"
    try:
        if image_url:
            await bot.send_photo(chat_id=_chat_id(), photo=image_url, caption=text)
        else:
            await bot.send_message(chat_id=_chat_id(), text=text)
    except Exception:
        # Fallback to text if image send fails
        await bot.send_message(chat_id=_chat_id(), text=text)


async def notify_publish_failure(bot: Bot, post_id: int, topic: str):
    """Notify that a post failed to publish, with one-tap recovery buttons."""
    text = f"PUBLISH FAILED: {topic}\nPost #{post_id}"
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Republish", callback_data=f"republish_{post_id}"),
        InlineKeyboardButton("Publish Now", callback_data=f"publishnow_{post_id}"),
    ]])
    await bot.send_message(chat_id=_chat_id(), text=text, reply_markup=keyboard)


# ── Health Command ──────────────────────────────────────────────────


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Run health checks and report to user."""
    _activate_brand_for_handler(context)
    if not _authorized(update):
        return
    try:
        from health import run_all_checks
        scheduler = context.application.bot_data.get("scheduler")
        result = run_all_checks(scheduler)

        lines = []
        for name, (ok, msg) in result["checks"].items():
            icon = "OK" if ok else "FAIL"
            lines.append(f"  {icon}  {name}: {msg}")

        status = "All systems operational" if result["healthy"] else "ISSUES DETECTED"
        await update.message.reply_text(f"{status}\n\n" + "\n".join(lines))
    except Exception as e:
        log.error(f"Health command failed: {e}")
        await update.message.reply_text(f"Health check error: {e}")


# ── Builder ──────────────────────────────────────────────────────────


def build_telegram_app(token: str | None = None) -> Application:
    """Build the Telegram Application with all handlers."""
    if token is None:
        token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("leads", leads_command))
    app.add_handler(CommandHandler("engage", engage_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CallbackQueryHandler(approval_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    return app
