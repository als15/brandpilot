import time

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import OrchestratorState
from db.connection import get_db


def _get_culinary_brief() -> str:
    """Run the culinary supervisor and return its brief for other agents to use."""
    from agents.culinary_supervisor import create_culinary_supervisor

    try:
        agent = create_culinary_supervisor()
        result = agent.invoke(
            {"messages": [{"role": "user", "content":
                "Review the current content feed and recent performance. "
                "Provide a concise culinary brief: what's missing, what's seasonal right now "
                "in Israel, and 3-5 specific content recommendations for the coming week."}]}
        )
        return result["messages"][-1].content
    except Exception as e:
        return f"(Culinary brief unavailable: {e})"


def _run_agent(agent_factory, task_message: str, state: OrchestratorState) -> dict:
    """Helper to invoke a sub-agent and capture results."""
    agent = agent_factory()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": task_message}]}
    )
    summary = result["messages"][-1].content
    return {"result_summary": summary, "messages": state.get("messages", [])}


def culinary_supervisor_node(state: OrchestratorState) -> dict:
    from agents.culinary_supervisor import create_culinary_supervisor

    return _run_agent(
        create_culinary_supervisor,
        "Review the current content feed and recent performance. Analyze what's missing, "
        "what's seasonal right now in Israel, what's trending in the Israeli food scene, "
        "and what our B2B audience (food trucks, coffee shops) wants to see. "
        "Provide a culinary brief with 3-5 specific content recommendations for the coming week, "
        "including dish/topic, why now, and suggested content pillar.",
        state,
    )


def content_strategist_node(state: OrchestratorState) -> dict:
    from agents.content_strategist import create_content_strategist

    # Get culinary guidance first
    culinary_brief = _get_culinary_brief()

    return _run_agent(
        create_content_strategist,
        "Plan content for this week. Check what we already have in the queue, "
        "review recent analytics, check our Instagram performance. "
        "Fill the week to targets: 5 feed posts (photo) + 7 stories. "
        "Rotate through content pillars and menu items.\n\n"
        "IMPORTANT — The Culinary Supervisor has reviewed our feed and provided this brief. "
        "Use these recommendations to guide your content choices:\n\n"
        f"--- CULINARY BRIEF ---\n{culinary_brief}\n--- END BRIEF ---",
        state,
    )


def design_review_node(state: OrchestratorState) -> dict:
    from agents.design_supervisor import create_design_supervisor

    return _run_agent(
        create_design_supervisor,
        "Review all draft posts in the content queue for brand consistency. "
        "Check captions (Hebrew tone, premium voice), visual directions (earthy, minimal, warm), "
        "and overall brand alignment. For posts that need changes, revise them directly. "
        "Add review notes to each post explaining your assessment.",
        state,
    )


def image_generator_node(state: OrchestratorState) -> dict:
    from agents.image_generator import create_image_generator

    return _run_agent(
        create_image_generator,
        "Check for draft posts without images and generate images for them. "
        "Get all drafts from the content queue, and for each one that has a "
        "visual_direction but no image_url, generate and upload an image.",
        state,
    )


def analytics_node(state: OrchestratorState) -> dict:
    from agents.analytics_agent import create_analytics_agent

    return _run_agent(
        create_analytics_agent,
        "Run a full analytics check. Get our current Instagram metrics, analyze "
        "each recent post's performance, compare with historical data, and save "
        "a snapshot with actionable recommendations.",
        state,
    )


def lead_generator_node(state: OrchestratorState) -> dict:
    from agents.lead_generator import create_lead_generator

    return _run_agent(
        create_lead_generator,
        "Find new potential B2B customers. Check existing leads to avoid duplicates, "
        "then search for food trucks, coffee shops, and small restaurants that could "
        "be sandwich supply customers. Add 3-5 quality leads.",
        state,
    )


def engagement_advisor_node(state: OrchestratorState) -> dict:
    from agents.engagement_advisor import create_engagement_advisor

    return _run_agent(
        create_engagement_advisor,
        "Create engagement suggestions. Review our current leads and pending tasks, "
        "then suggest 5-10 new engagement actions (comments, follows, likes) that "
        "will help build relationships with potential customers.",
        state,
    )


def content_publisher_node(state: OrchestratorState) -> dict:
    from agents.content_publisher import create_content_publisher

    return _run_agent(
        create_content_publisher,
        "Check for approved feed posts (content_type='photo') and publish them to Instagram.",
        state,
    )


def content_reviewer_node(state: OrchestratorState) -> dict:
    from agents.content_reviewer import create_content_reviewer

    # Get culinary guidance for review context
    culinary_brief = _get_culinary_brief()

    return _run_agent(
        create_content_reviewer,
        "Review this week's published content performance. Compare against benchmarks. "
        "If posts are underperforming, revise upcoming scheduled content (captions, "
        "visual directions, pillars) and reset to draft for re-approval. "
        "If performance is fine, report no changes needed.\n\n"
        "The Culinary Supervisor has provided feedback on our content direction. "
        "Consider this when deciding what to revise:\n\n"
        f"--- CULINARY BRIEF ---\n{culinary_brief}\n--- END BRIEF ---",
        state,
    )


def story_publisher_node(state: OrchestratorState) -> dict:
    from agents.content_publisher import create_story_publisher

    return _run_agent(
        create_story_publisher,
        "Check for approved stories (content_type='story') and publish them to Instagram.",
        state,
    )


def router(state: OrchestratorState) -> str:
    routing = {
        "culinary_review": "culinary_supervisor",
        "content_planning": "content_strategist",
        "design_review": "design_supervisor",
        "image_generation": "image_generator",
        "analytics": "analytics_agent",
        "lead_gen": "lead_generator",
        "engagement": "engagement_advisor",
        "publish": "content_publisher",
        "publish_stories": "story_publisher",
        "content_review": "content_reviewer",
    }
    return routing.get(state["task_type"], END)


def build_orchestrator():
    graph = StateGraph(OrchestratorState)

    graph.add_node("culinary_supervisor", culinary_supervisor_node)
    graph.add_node("content_strategist", content_strategist_node)
    graph.add_node("design_supervisor", design_review_node)
    graph.add_node("image_generator", image_generator_node)
    graph.add_node("analytics_agent", analytics_node)
    graph.add_node("lead_generator", lead_generator_node)
    graph.add_node("engagement_advisor", engagement_advisor_node)
    graph.add_node("content_publisher", content_publisher_node)
    graph.add_node("story_publisher", story_publisher_node)
    graph.add_node("content_reviewer", content_reviewer_node)

    graph.add_conditional_edges(START, router)

    for node in [
        "culinary_supervisor",
        "content_strategist",
        "design_supervisor",
        "image_generator",
        "analytics_agent",
        "lead_generator",
        "engagement_advisor",
        "content_publisher",
        "story_publisher",
        "content_reviewer",
    ]:
        graph.add_edge(node, END)

    checkpointer = MemorySaver()
    return graph.compile(checkpointer=checkpointer)


def run_task(task_type: str) -> str:
    """Run a specific task through the orchestrator. Returns the result summary."""
    app = build_orchestrator()
    thread_id = f"{task_type}_{int(time.time())}"
    config = {"configurable": {"thread_id": thread_id}}

    start = time.time()
    try:
        result = app.invoke(
            {"task_type": task_type, "messages": [], "result_summary": ""},
            config=config,
        )
        duration = time.time() - start
        summary = result.get("result_summary", "No summary.")

        # Log the run
        db = get_db()
        db.execute(
            "INSERT INTO run_log (task_type, status, duration_seconds, summary) VALUES (?, ?, ?, ?)",
            (task_type, "completed", duration, summary[:500]),
        )
        db.commit()

        return summary
    except Exception as e:
        duration = time.time() - start
        db = get_db()
        db.execute(
            "INSERT INTO run_log (task_type, status, duration_seconds, error) VALUES (?, ?, ?, ?)",
            (task_type, "failed", duration, str(e)[:500]),
        )
        db.commit()
        raise
