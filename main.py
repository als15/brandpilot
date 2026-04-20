"""Interactive CLI and manual task runner for Instagram agents."""

from dotenv import load_dotenv

load_dotenv()

import sys
from brands.loader import init_brand
from db.schema import init_db
from graph.orchestrator import run_task


TASKS = {
    "content": "content_planning",
    "design": "design_review",
    "images": "image_generation",
    "analytics": "analytics",
    "leads": "lead_gen",
    "engagement": "engagement",
    "publish": "publish",
    "stories": "publish_stories",
    "review": "content_review",
}


def interactive_mode():
    """Run a specific agent interactively with custom prompts."""
    from brands.loader import brand_config
    from agents.content_strategist import create_content_strategist
    from agents.analytics_agent import create_analytics_agent
    from agents.lead_generator import create_lead_generator
    from agents.engagement_advisor import create_engagement_advisor

    agents = {
        "strategist": ("Content Strategist", create_content_strategist),
        "analytics": ("Analytics Agent", create_analytics_agent),
        "leads": ("Lead Generator", create_lead_generator),
        "engagement": ("Engagement Advisor", create_engagement_advisor),
    }

    print(f"\n=== {brand_config.identity.name_en} - Interactive Mode ===")
    print("\nAvailable agents:")
    for key, (name, _) in agents.items():
        print(f"  {key:15s} - {name}")
    print()

    choice = input("Select agent: ").strip().lower()
    if choice not in agents:
        print(f"Unknown agent: {choice}")
        return

    name, factory = agents[choice]
    agent = factory()

    print(f"\n--- {name} ---")
    print("Type 'quit' to exit\n")

    messages = []
    while True:
        user_input = input("You: ").strip()
        if user_input.lower() in ("quit", "exit", "q"):
            break
        if not user_input:
            continue

        messages.append({"role": "user", "content": user_input})
        print(f"\n{name}: ", end="", flush=True)

        result = agent.invoke({"messages": messages})
        ai_message = result["messages"][-1]
        print(ai_message.content)
        messages = result["messages"]


def _get_task_arg() -> str | None:
    """Extract the task argument, skipping --brand and its value."""
    skip_next = False
    for arg in sys.argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if arg == "--brand":
            skip_next = True
            continue
        return arg
    return None


def main():
    bc = init_brand()
    init_db()

    task_arg = _get_task_arg()

    if task_arg:
        if task_arg == "interactive":
            interactive_mode()
            return

        if task_arg in TASKS:
            task_type = TASKS[task_arg]
            print(f"[{bc.identity.name_en}] Running task: {task_type}...")
            summary = run_task(task_type, bc.slug)
            print(f"\nResult:\n{summary}")
            return

        print(f"Unknown command: {task_arg}")
        print()

    print(f"=== {bc.identity.name_en} - Instagram Agent System ===\n")
    print("Usage:")
    print("  python main.py [--brand <slug>] interactive     - Chat with a specific agent")
    print("  python main.py [--brand <slug>] content         - Run content planning")
    print("  python main.py [--brand <slug>] design          - Run design review on drafts")
    print("  python main.py [--brand <slug>] images          - Generate images for drafts")
    print("  python main.py [--brand <slug>] analytics       - Run analytics")
    print("  python main.py [--brand <slug>] leads           - Run lead generation")
    print("  python main.py [--brand <slug>] engagement      - Run engagement advisor")
    print("  python main.py [--brand <slug>] publish         - Publish approved feed posts")
    print("  python main.py [--brand <slug>] stories         - Publish approved stories")
    print("  python main.py [--brand <slug>] review          - Review performance & adjust")
    print()
    print("Other tools:")
    print("  python review.py               - Review dashboard (queue, leads, tasks)")
    print("  python daemon.py --brand <slug> - Start autonomous scheduler + Telegram bot")


if __name__ == "__main__":
    main()
