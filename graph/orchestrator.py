import time

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from graph.state import OrchestratorState
from db.schema import init_db
from db.connection import get_db


def _run_agent(agent_factory, task_message: str, state: OrchestratorState) -> dict:
    """Helper to invoke a sub-agent and capture results."""
    agent = agent_factory()
    result = agent.invoke(
        {"messages": [{"role": "user", "content": task_message}]}
    )
    summary = result["messages"][-1].content
    return {"result_summary": summary, "messages": state.get("messages", [])}


def content_strategist_node(state: OrchestratorState) -> dict:
    from agents.content_strategist import create_content_strategist

    return _run_agent(
        create_content_strategist,
        "Plan new content for this week. Check what we already have in the queue, "
        "review recent analytics, check our Instagram performance, and add 3-5 new "
        "posts to the content queue. Rotate through content pillars.",
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
        "Check for approved posts and publish them to Instagram.",
        state,
    )


def router(state: OrchestratorState) -> str:
    routing = {
        "content_planning": "content_strategist",
        "design_review": "design_supervisor",
        "image_generation": "image_generator",
        "analytics": "analytics_agent",
        "lead_gen": "lead_generator",
        "engagement": "engagement_advisor",
        "publish": "content_publisher",
    }
    return routing.get(state["task_type"], END)


def build_orchestrator():
    init_db()

    graph = StateGraph(OrchestratorState)

    graph.add_node("content_strategist", content_strategist_node)
    graph.add_node("design_supervisor", design_review_node)
    graph.add_node("image_generator", image_generator_node)
    graph.add_node("analytics_agent", analytics_node)
    graph.add_node("lead_generator", lead_generator_node)
    graph.add_node("engagement_advisor", engagement_advisor_node)
    graph.add_node("content_publisher", content_publisher_node)

    graph.add_conditional_edges(START, router)

    for node in [
        "content_strategist",
        "design_supervisor",
        "image_generator",
        "analytics_agent",
        "lead_generator",
        "engagement_advisor",
        "content_publisher",
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
