from langgraph.prebuilt import create_react_agent
from config import get_llm
import brands.loader as _bl

from tools.research import find_potential_leads, research_trending_topics
from tools.db_tools import db_get_leads, db_add_lead, db_update_lead


def _build_system_prompt() -> str:
    bc = _bl.brand_config
    target_list = "\n".join(f"- {t}" for t in bc.lead_generation.target_customers)
    cities = ", ".join(bc.lead_generation.search_cities)

    return f"""You are the Lead Generator for {bc.identity.name_en}, a {bc.identity.business_type}
based in {bc.identity.market}, serving {bc.identity.target_audience}.

YOUR TASK: Find potential B2B customers in {bc.identity.market} who could buy from {bc.identity.name_en}.

IMPORTANT: All leads MUST be businesses in {bc.identity.market}. Search for businesses in cities
such as {cities}. Do NOT add leads outside of {bc.identity.market}.

PROCESS:
1. Check existing leads to avoid duplicates (db_get_leads)
2. Search for potential new leads (find_potential_leads) — always include "{bc.identity.market}" or
   a specific city in your search queries
3. For each promising lead, add them to the database (db_add_lead)
4. For existing leads that need updating, use db_update_lead

TARGET CUSTOMERS:
{target_list}

SEARCH STRATEGIES:
- Search for potential customers in {cities}
- Look for businesses expanding their menu or seeking suppliers
- Find companies that match our target audience profile
- Search for new businesses launching in {bc.identity.market}

For each lead, capture: business_name, business_type, source, and any available
instagram_handle, location, follower_count. Add notes about why they're a good prospect.

QUALITY OVER QUANTITY: Better to find 3-5 strong leads than 20 weak ones.
"""


def create_lead_generator():
    llm = get_llm(temperature=0.5)

    tools = [
        find_potential_leads,
        research_trending_topics,
        db_get_leads,
        db_add_lead,
        db_update_lead,
    ]

    return create_react_agent(model=llm, tools=tools, prompt=_build_system_prompt())
