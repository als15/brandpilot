from langgraph.prebuilt import create_react_agent
from config import get_llm
import brands.loader as _bl

from tools.research import research_trending_topics
from tools.db_tools import (
    db_get_leads,
    db_add_engagement_task,
    db_get_engagement_tasks,
)


def _build_system_prompt() -> str:
    bc = _bl.brand_config
    return f"""You are the Engagement Advisor for {bc.identity.name_en}, a {bc.identity.business_type}.

YOUR TASK: Suggest Instagram engagement actions that build relationships and visibility
with potential B2B customers ({bc.identity.target_audience}).

PROCESS:
1. Review current leads - these are engagement priorities (db_get_leads)
2. Check what engagement tasks are already pending (db_get_engagement_tasks)
3. Research relevant trending topics or hashtags (research_trending_topics)
4. Create new engagement suggestions (db_add_engagement_task) for:
   - Commenting on lead/prospect posts
   - Following relevant accounts
   - Engaging with industry hashtag posts

COMMENT GUIDELINES:
- Be genuine, never salesy in comments
- Reference something specific about their post
- Position {bc.identity.name_en} as a fellow industry insider
- Keep suggested comments under 150 characters

ACTION TYPES: 'comment', 'like', 'follow', 'dm'

For each task, include a clear reason WHY this engagement matters for the business.
Focus on leads with status 'discovered' or 'researched' - these need relationship building.
Create 5-10 engagement suggestions per run.
"""


def create_engagement_advisor():
    llm = get_llm(temperature=0.7)

    tools = [
        research_trending_topics,
        db_get_leads,
        db_add_engagement_task,
        db_get_engagement_tasks,
    ]

    return create_react_agent(model=llm, tools=tools, prompt=_build_system_prompt())
