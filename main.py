"""Interactive CLI and manual task runner for Capa & Co Instagram agents."""

from dotenv import load_dotenv

load_dotenv()

import sys
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
}


def interactive_mode():
    """Run a specific agent interactively with custom prompts."""
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

    print("\n=== Capa & Co - Interactive Mode ===")
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


def main():
    init_db()

    if len(sys.argv) > 1:
        arg = sys.argv[1]

        if arg == "interactive":
            interactive_mode()
            return

        if arg in TASKS:
            task_type = TASKS[arg]
            print(f"Running task: {task_type}...")
            summary = run_task(task_type)
            print(f"\nResult:\n{summary}")
            return

        print(f"Unknown command: {arg}")
        print()

    print("=== Capa & Co - Instagram Agent System ===\n")
    print("Usage:")
    print("  python main.py interactive     - Chat with a specific agent")
    print("  python main.py content         - Run content planning")
    print("  python main.py design          - Run design review on drafts")
    print("  python main.py images          - Generate images for drafts")
    print("  python main.py analytics       - Run analytics")
    print("  python main.py leads           - Run lead generation")
    print("  python main.py engagement      - Run engagement advisor")
    print("  python main.py publish         - Publish approved posts")
    print()
    print("Other tools:")
    print("  python review.py               - Review dashboard (queue, leads, tasks)")
    print("  python daemon.py               - Start autonomous scheduler + Telegram bot")


if __name__ == "__main__":
    main()
