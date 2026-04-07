# Robustness & Observability Overhaul

## Context

Two consecutive days of silent publishing failures (Apr 6-7) caused by a database deadlock that went undetected. The deadlock itself is fixed (`b85ac93`), but the root issue is systemic: the system has no way to tell the user "I'm broken" or "I published your post." All success is silent, failures can go unnoticed, and there's no self-monitoring.

Additionally, the Meta access token expired on Apr 3 and went undetected — compounding the publishing outage.

**Goal:** Make the system loud when things break, confirmatory when things work, and self-healing where possible.

---

## Phase 1: Database Connection Resilience
> Foundation — everything else depends on a reliable DB connection

**File: `db/connection.py`**

- Add `_is_connection_alive()` helper: runs `SELECT 1`, catches `OperationalError`/`InterfaceError`
- Modify `get_db()`: test cached connection before returning; if dead, close + reconnect
- Postgres: add `connect_timeout=10` and `SET statement_timeout = '120000'` (120s) after connecting

---

## Phase 2: Request Timeouts
> Prevent silent hangs on external services

**File: `tools/instagram.py`**
- Add `timeout=30` to all `requests.get()` and `requests.post()` calls (6 call sites)

**File: `tools/image_gen.py`**
- Add `timeout=60` to `requests.get(fal_url)` in `_rehost_image()` (line 89)

**File: `daemon.py`**
- Wrap `loop.run_in_executor(None, run_task, task_type)` in `asyncio.wait_for(timeout=...)`:
  - publish/publish_stories: 300s (5 min)
  - image_generation/content_planning/content_review: 600s (10 min)
  - default: 300s
- On `TimeoutError`: log to `run_log` with `status='timeout'`, send `notify_error` via Telegram

---

## Phase 3: Publish Notifications
> "Tell me when content is published or fails"

**File: `daemon.py`**
- Rename `QUIET_TASKS` to `SKIP_WHEN_EMPTY` — keep the "skip if nothing to publish" logic, but decouple from notification suppression
- After successful `run_task` for publish tasks, query DB for posts published in last 5 min (use `published_at`), and for posts with `status='failed'` — send per-post notifications

**File: `telegram_bot.py`**
- Add `notify_publish_success(bot, post_id, topic, image_url)`: sends photo message with topic/ID confirmation
- Add `notify_publish_failure(bot, post_id, topic, error)`: sends text alert with error details

Postgres vs SQLite datetime: use `_is_postgres()` to branch the "last 5 minutes" query.

---

## Phase 4: Health Checking System
> Proactive service monitoring + on-demand status reports

**New file: `health.py`** (top-level, deterministic — no LLM calls)

Functions returning `(ok: bool, message: str)`:
- `check_db()` — `SELECT 1`
- `check_instagram_token()` — `GET /me` with 5s timeout
- `check_scheduler(scheduler)` — verify running + has jobs
- `check_recent_activity()` — query run_log for last 24h; flag if 0 completed runs or >50% failure rate
- `check_overdue_posts()` — approved posts with scheduled_date/time > 2h overdue (catches the exact scenario from this incident)
- `run_all_checks(scheduler)` — runs all, returns `{"healthy": bool, "checks": {...}}`

**File: `daemon.py`**
- Add scheduled health check job: every 30 min via APScheduler
- Writes heartbeat to `run_log` (`task_type='heartbeat'`, `status='ok'`)
- Runs `run_all_checks()` — only sends Telegram alert when something FAILS (silent when healthy)

**File: `web/auth.py`**
- Replace dummy `/health` with real checks (DB + scheduler + recent heartbeat)
- Return `200` if healthy, `503` if not — Railway can detect and restart

**File: `telegram_bot.py`**
- Add `/health` command handler: runs `run_all_checks()`, formats readable status report
- Register in `build_telegram_app()`
- Pass scheduler via `app.bot_data["scheduler"]`

**File: `railway.toml`**
- Add `healthcheckPath = "/health"` under `[deploy]` if supported

---

## Phase 5: Failed Publish Auto-Retry
> Self-healing for transient failures

**File: `db/schema.py`**
- Add column: `retry_count INTEGER DEFAULT 0` to `content_queue`

**File: `daemon.py`**
- Before running publish task: `UPDATE content_queue SET status='approved' WHERE status='failed' AND retry_count < 3`
- Existing publisher agent picks these up naturally

**File: `tools/db_tools.py`**
- In `db_update_post_status`: when status='failed', increment `retry_count`

**File: `telegram_bot.py`**
- When post hits retry_count=3, send escalation: "Post #N failed 3 times, needs manual intervention"

---

## Phase 6: Error Categorization
> Better diagnostics in run_log

**File: `db/schema.py`**
- Add column: `error_category TEXT` to `run_log`

**File: `graph/orchestrator.py`**
- Add `_categorize_error(e)`: inspects exception type + message
  - Categories: `timeout`, `db_error`, `auth_error`, `api_error`, `llm_error`, `unknown`
- Include in `INSERT INTO run_log` on failure

---

## Files Changed Summary

| File | Change |
|------|--------|
| `db/connection.py` | Connection liveness test, auto-reconnect, timeouts |
| `tools/instagram.py` | `timeout=30` on all HTTP calls |
| `tools/image_gen.py` | `timeout=60` on image download |
| `daemon.py` | Task timeouts, publish notifications, health job, retry reset |
| `telegram_bot.py` | `notify_publish_success/failure`, `/health` command |
| `health.py` (new) | All health check functions |
| `web/auth.py` | Real `/health` endpoint |
| `db/schema.py` | `retry_count` + `error_category` columns |
| `tools/db_tools.py` | Increment retry_count on failure |
| `graph/orchestrator.py` | Error categorization |
| `railway.toml` | Health check path |

## Implementation Order

Phase 1 -> 2 -> 3 -> 4 -> 5 -> 6 (each builds on the previous)

## Verification

After deploying to Railway:
1. Check `/health` endpoint returns real status
2. Send `/health` in Telegram — should get full system report
3. Wait for next publish window — should receive photo confirmation in Telegram
4. Check Railway logs for heartbeat entries every 30 min
5. Manually set a post to `status='failed'` with `retry_count=0` — verify retry
6. Query `run_log` for `error_category` column after any failure
