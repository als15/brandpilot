"""Normalize content_queue scheduling for a single week.

The content strategist LLM can double-book days (two photos on Monday, two
stories on Tuesday) even when the prompt says otherwise. This module applies
a deterministic rebalance so the week respects:

- Photos: at most 1 per day, capped at photo_days across the week.
- Stories: at most 1 per day, one per day for story_days of the week.

Published posts are pinned (never moved). Surplus posts beyond the weekly cap
are unscheduled (date/time cleared) so they surface for human handling
instead of piling onto a random day.
"""

from __future__ import annotations

from datetime import date, timedelta


def _norm_date(value) -> str:
    return str(value)[:10] if value else ""


def _norm_time(value) -> str:
    return str(value)[:5] if value else ""


def compute_reschedule(
    posts: list[dict],
    week_start: date,
    *,
    photo_time: str = "07:00",
    story_time: str = "09:00",
    photo_days: int = 5,
    story_days: int = 7,
) -> list[tuple[int, str | None, str | None]]:
    """Return the list of (post_id, new_scheduled_date, new_scheduled_time)
    updates needed so the given week satisfies the per-day caps.

    A ``new_scheduled_date`` of ``None`` means the post should be unscheduled
    (its date and time set to NULL) because there is no slot left in the week.

    Only posts whose current ``scheduled_date`` falls inside the week are
    considered; published posts are pinned in place.
    """
    week_iso = [(week_start + timedelta(days=i)).isoformat() for i in range(7)]
    week_set = set(week_iso)

    moves: list[tuple[int, str | None, str | None]] = []

    for ct, target_time, limit in (
        ("story", story_time, story_days),
        ("photo", photo_time, photo_days),
    ):
        pinned_days: set[str] = set()
        movable: list[dict] = []

        for p in posts:
            if (p.get("content_type") or "").lower() != ct:
                continue
            sd = _norm_date(p.get("scheduled_date"))
            if sd not in week_set:
                continue
            if (p.get("status") or "").lower() == "published":
                pinned_days.add(sd)
                continue
            movable.append(p)

        # Stable order: original day, then time, then id. Posts already on a
        # valid day keep their slot when possible.
        movable.sort(key=lambda p: (
            _norm_date(p.get("scheduled_date")),
            _norm_time(p.get("scheduled_time")),
            p.get("id", 0),
        ))

        kept: dict[str, int] = {d: 0 for d in week_iso}
        for d in pinned_days:
            kept[d] = 1

        assignments: list[tuple[dict, str | None]] = []
        overflow: list[dict] = []

        # Pass 1: keep posts on their original day if capacity allows.
        for p in movable:
            d = _norm_date(p.get("scheduled_date"))
            if kept[d] == 0:
                kept[d] = 1
                assignments.append((p, d))
            else:
                overflow.append(p)

        # Pass 2: fill empty days from overflow, walking Mon→Sun.
        slots_used = sum(1 for v in kept.values() if v > 0)
        for d in week_iso:
            if kept[d] or slots_used >= limit:
                continue
            if not overflow:
                break
            p = overflow.pop(0)
            kept[d] = 1
            slots_used += 1
            assignments.append((p, d))

        # Remaining overflow has no slot this week → unschedule.
        for p in overflow:
            assignments.append((p, None))

        for p, new_date in assignments:
            old_date = _norm_date(p.get("scheduled_date"))
            old_time = _norm_time(p.get("scheduled_time"))
            if new_date is None:
                if old_date or old_time:
                    moves.append((p["id"], None, None))
            elif old_date != new_date or old_time != target_time:
                moves.append((p["id"], new_date, target_time))

    return moves


def current_week_start(today: date | None = None) -> date:
    """Monday of the ISO week containing ``today`` (defaults to today)."""
    t = today or date.today()
    return t - timedelta(days=t.weekday())
