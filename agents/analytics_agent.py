from langgraph.prebuilt import create_react_agent
from config import get_llm

from tools.instagram import (
    get_instagram_profile,
    get_recent_media,
    get_media_insights,
    get_account_insights,
)
from tools.db_tools import (
    db_save_analytics_snapshot,
    db_save_post_performance,
    db_get_analytics_summary,
    db_get_content_queue,
    db_get_post_performance,
)

SYSTEM_PROMPT = """You are the Analytics Agent for Capa & Co, a B2B sandwich supplier.

YOUR TASK: Track Instagram performance and identify what content works best.

PROCESS:
1. Get current account metrics (get_instagram_profile, get_account_insights)
2. Get recent post performance (get_recent_media, then get_media_insights for top posts)
3. Store individual post metrics (db_save_post_performance for each post)
4. Compare with historical data (db_get_analytics_summary)
5. Save a snapshot with recommendations (db_save_analytics_snapshot)

ANALYSIS FOCUS:
- Which content type/pillar gets best engagement?
- Best posting times based on data?
- Follower growth trend (compare with previous snapshots)?
- Write 2-3 specific, actionable recommendations.

For avg_engagement_rate, calculate: (total likes + comments) / follower_count / number_of_posts.
If insights API returns errors for some posts, skip those and work with what you have.
Note: The account insights API returns "views" (not "impressions") and "reach". Use these metric names in your reports.
"""


def create_analytics_agent():
    llm = get_llm(temperature=0.3)

    tools = [
        get_instagram_profile,
        get_recent_media,
        get_media_insights,
        get_account_insights,
        db_save_analytics_snapshot,
        db_save_post_performance,
        db_get_analytics_summary,
        db_get_content_queue,
        db_get_post_performance,
    ]

    return create_react_agent(model=llm, tools=tools, prompt=SYSTEM_PROMPT)
