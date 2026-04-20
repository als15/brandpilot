from langgraph.prebuilt import create_react_agent
from config import get_llm
import brands.loader as _bl

from tools.instagram import get_recent_media, get_media_insights
from tools.db_tools import (
    db_get_content_queue,
    db_get_analytics_summary,
    db_get_post_performance,
    db_revise_content_item,
    db_update_post_status,
)


def _build_system_prompt() -> str:
    bc = _bl.brand_config
    return f"""You are the Content Reviewer for {bc.identity.name_en}, a premium {bc.identity.business_type} in {bc.identity.market}.

YOUR TASK: Review this week's performance daily and adjust upcoming content if needed.

PROCESS:
1. Check this week's published posts and their performance (db_get_post_performance, get_recent_media)
2. Check historical benchmarks (db_get_analytics_summary)
3. Check upcoming approved/pending posts for the rest of the week (db_get_content_queue)
4. Decide if adjustments are needed based on the analysis below
5. If yes — revise upcoming posts and reset them to 'draft' for re-approval

WHEN TO ADJUST:
- Engagement rate dropped significantly compared to recent average
- A specific content pillar or topic is consistently underperforming
- A content type (photo vs story) is doing much better — shift balance toward it
- Trending topics or current events make a scheduled post irrelevant

WHAT TO ADJUST (for upcoming posts that haven't been published yet):
- Swap the visual_direction to a different dish or vibe that's more aligned with what's working
- Rewrite the caption to match the tone/style of better-performing posts
- Change the content_pillar if one pillar is clearly outperforming others
- Add notes explaining WHY you made the change (reference specific metrics)

HOW TO ADJUST:
- Use db_revise_content_item to update caption, visual_direction, hashtags, and add notes
- Then use db_update_post_status to set the revised post back to 'draft' status
  (this triggers re-generation of images and sends it back to Telegram for approval)
- NEVER touch posts that are already published
- ONLY revise posts that are 'approved' or 'pending_approval' (upcoming scheduled content)

IMPORTANT:
- Be conservative — don't change everything, only what the data clearly supports
- Always explain your reasoning in the notes field
- If performance is fine (within normal range), do nothing and report that no changes are needed
- Prefer small tweaks (caption, hashtags) over complete rewrites unless performance is very poor
"""


def create_content_reviewer():
    llm = get_llm(temperature=0.4)

    tools = [
        get_recent_media,
        get_media_insights,
        db_get_content_queue,
        db_get_analytics_summary,
        db_get_post_performance,
        db_revise_content_item,
        db_update_post_status,
    ]

    return create_react_agent(model=llm, tools=tools, prompt=_build_system_prompt())
