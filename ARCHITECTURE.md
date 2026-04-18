# Brandpilot Agent Architecture

Brandpilot is an autonomous Instagram content management system built with **LangGraph**. It runs **10 specialized agents** orchestrated by a daemon scheduler, communicating through a shared database.

## The Agents

| Agent | LLM? | Role |
|-------|-------|------|
| **Culinary Supervisor** | Yes | Analyzes food market trends, seasonal patterns, and competitive landscape. Produces a brief that guides content creation. |
| **Content Strategist** | Yes | Creates the weekly content plan — exactly 5 feed posts + 7 stories — and inserts them as drafts into the content queue. |
| **Design Supervisor** | Yes | Reviews drafts for brand consistency (caption tone, visual direction, color palette). Can PASS or REVISE items. |
| **Image Generator** | Yes | Takes approved visual directions, builds expert prompts, generates images via AI, uploads to cloud storage. Moves posts to `pending_approval`. |
| **Analytics Agent** | Yes | Pulls Instagram metrics (reach, engagement, followers) and saves snapshots to the database. |
| **Content Reviewer** | Yes | Compares recent post performance against benchmarks. Revises underperforming upcoming posts. |
| **Lead Generator** | Yes | Finds B2B prospects (food trucks, cafes, restaurants) in configured cities. |
| **Engagement Advisor** | Yes | Creates engagement task suggestions (comments, follows, DMs) targeting discovered leads. |
| **Content Publisher** | No | Deterministic — publishes due approved feed posts to Instagram via the API. |
| **Story Publisher** | No | Deterministic — publishes due approved stories to Instagram. |

## How They Communicate

Agents **never talk directly to each other**. All data flows through the **shared database** (Postgres or SQLite):

- `content_queue` — drafts, approved posts, published posts
- `leads` — B2B prospects
- `engagement_tasks` — pending engagement actions
- `analytics_snapshots` — account-level performance
- `post_performance` — per-post metrics
- `run_log` — task execution history

## The Weekly Flow

```
SUNDAY — Weekly Planning Session
──────────────────────────────────────────
06:30  Culinary Supervisor    → analyzes trends, writes brief
07:00  Content Strategist     → reads brief, creates 12 draft items
08:00  Design Supervisor      → reviews drafts, revises if needed
09:00  Image Generator        → generates images for all drafts
                                 → moves posts to pending_approval
                                 → sends Telegram approval notifications

DAILY — Publishing & Review
──────────────────────────────────────────
6am-8pm (every 2h)  Content Publisher   → publishes due approved feed posts
6am-8pm (every 2h)  Story Publisher     → publishes due approved stories
18:00               Analytics Agent     → collects Instagram metrics
19:00               Content Reviewer    → revises underperformers
                                          (can trigger re-imaging)

WEEKLY — Growth
──────────────────────────────────────────
Wed 10:00   Lead Generator       → finds 3-5 new B2B prospects
Tue/Thu     Engagement Advisor   → creates 5-10 engagement suggestions
```

## Task Dependencies

The daemon enforces hard dependencies before running a task:

```
Design Supervisor  →  requires Content Strategist to have succeeded that day
Image Generator    →  requires Design Supervisor to have succeeded that day
```

If a dependency hasn't completed, the downstream task is **skipped** (not queued).

## Orchestration Layer

```
┌──────────────────────────┐
│   daemon.py (APScheduler)│  ← cron triggers per brand
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│  graph/orchestrator.py   │  ← LangGraph StateGraph routes task_type
│  (START → router → agent │     to the correct agent node
│   → END)                 │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│  agents/*.py             │  ← ReAct agents with tools
│  (LangGraph prebuilt)    │
└────────────┬─────────────┘
             │
             ▼
┌──────────────────────────┐
│  Shared Database         │  ← all agent I/O flows through here
│  + Instagram API         │
│  + Image Gen API         │
│  + Telegram Notifications│
└──────────────────────────┘
```

## Multi-Brand Support

Each brand (e.g., "Capa & Co", "Mila") has:

- Its own `brands/<slug>/config.yaml` (schedule, voice, visual identity, content strategy)
- Its own `.env` (Instagram token, Telegram bot token, API keys)
- Its own set of scheduled jobs (scoped by `brand_id`)
- Its own Telegram bot for notifications

## Safety Mechanisms

- **Idempotency** — publishers check `instagram_media_id` to avoid double-posting
- **Daily limits** — max posts/stories per day enforced
- **Retry logic** — failed publishes retry up to 3 times
- **Timeouts** — image generation: 600s, everything else: 300s
- **Error categorization** — auth, API, timeout, DB, LLM, unknown
- **Graceful degradation** — one failure doesn't crash the system

## Key Files

| File | Purpose |
|------|---------|
| `daemon.py` | Main autonomous scheduler + Telegram bot + web server |
| `graph/orchestrator.py` | Task routing state machine |
| `agents/*.py` | 10 agent definitions |
| `brands/loader.py` | Brand configuration singleton |
| `db/schema.py` | Database table definitions |
| `db/connection.py` | Postgres/SQLite abstraction |
| `tools/db_tools.py` | Database query tools for agents |
| `tools/instagram.py` | Instagram API wrappers |
| `tools/content_guide.py` | Image prompt builder |
| `tools/image_gen.py` | Image generation + hosting |
| `tools/research.py` | Web search/lead finding |

---

The key insight: it's a **data-driven pipeline**, not a chatbot. Agents are scheduled like cron jobs, each reading from and writing to the database, with the daemon ensuring correct ordering and the Telegram bot providing human-in-the-loop approval before anything gets published.
