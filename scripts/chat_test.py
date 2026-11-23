"""Talk to an agent like real hotel staff would, and time every reply.

Each scenario below is a NEW chat (a new session), with follow-up questions, so the
agent sees its own earlier answers just like in the real chat window. Every chat is
saved like a real preview chat, so you can review it later in the dashboard:
    Dashboard -> agent -> Conversations -> (open a chat) -> replies with "first words" times.

Usage (Ollama running, LLM_PROVIDER=local, the model already pulled):
    python scripts/chat_test.py              # agent 1, all scenarios
    python scripts/chat_test.py 2            # another agent id

Close heavy apps (browser) first: on an 8 GB machine, swapping makes every reply slow.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from apps.core import agent as hotel_agent  # noqa: E402
from apps.core.services import agent_service, conversation_service, hotel_service  # noqa: E402
from helpers.database import get_platform_db  # noqa: E402

MAX_HISTORY_TURNS = 20   # same as the chat route

# One scenario = one new chat. Questions are asked in order, as follow-ups.
SCENARIOS = {
    "Sales check": [
        "hi",
        "what were last month's sales?",
        "and this month so far?",
        "how many bookings were cancelled last month?",
    ],
    "Bookings": [
        "show the cancelled bookings",
        "which guest made booking 46?",
        "show upcoming confirmed bookings for the next 30 days",
    ],
    "Today's operations": [
        "how many guests are checked in right now?",
        "any arrivals or departures today?",
        "how many rooms are free tomorrow?",
        "is a sea view room free this weekend?",
    ],
    "Hotel info": [
        "what time is check-out?",
        "can guests bring a dog?",
        "do we have a gym?",
        "what's the weather in Goa today?",
    ],
}


def ask(agent, conversation, question):
    """One turn, exactly like the streamed chat route: history from the DB, answer stored with timings."""
    history = conversation_service.get_history(conversation["id"], MAX_HISTORY_TURNS)
    conversation_service.add_message(conversation["id"], "user", question)

    started, first_ms, reply, error = time.perf_counter(), None, None, None
    try:
        for event in hotel_agent.respond_stream(agent, question, history):
            if event["type"] in ("token", "table") and first_ms is None:
                first_ms = int((time.perf_counter() - started) * 1000)
            elif event["type"] == "final":
                reply = event["reply"]
    except hotel_agent.AgentError as exc:
        error = str(exc)
    total_ms = int((time.perf_counter() - started) * 1000)

    if reply:
        conversation_service.add_message(
            conversation["id"], "assistant", reply.text, model=reply.model,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
            latency_ms=total_ms, tool_calls=reply.tool_calls, first_token_ms=first_ms,
        )
        tools = [f"{c['name']}({', '.join(f'{k}={v}' for k, v in c['args'].items())})" for c in reply.tool_calls]
        return reply.text, tools, first_ms, total_ms
    conversation_service.add_message(conversation["id"], "assistant", error, status="error",
                                     latency_ms=total_ms, first_token_ms=first_ms)
    return f"ERROR: {error}", [], first_ms, total_ms


def run(agent_id):
    app = create_app()
    rows = []
    with app.app_context():
        agent = agent_service.get_agent(agent_id)
        hotel = hotel_service.get_hotel(agent["hotel_id"])
        admin = get_platform_db().execute(
            "SELECT id FROM users WHERE role = 'platform_admin' ORDER BY id LIMIT 1").fetchone()
        print(f"Agent {agent['agent_name']} @ {hotel['name']} | provider {app.config['LLM_PROVIDER']}"
              f" | model {app.config['OLLAMA_MODEL']} | history {app.config['OLLAMA_MAX_HISTORY']} messages")

        t = time.perf_counter()
        hotel_agent.respond(agent, "hi", [])   # load the model into RAM first (not counted)
        print(f"(model loaded and warmed in {time.perf_counter() - t:.0f}s)")

        for title, questions in SCENARIOS.items():
            guest = conversation_service.get_or_create_guest(None)
            conversation = conversation_service.start_conversation(
                agent, guest["id"], channel="preview", started_by=admin["id"] if admin else None,
                greeting=hotel_agent.greeting_for(agent, hotel),
            )
            print(f"\n{'=' * 78}\n{title}   (conversation #{conversation['id']})\n{'=' * 78}")
            for question in questions:
                text, tools, first_ms, total_ms = ask(agent, conversation, question)
                rows.append((title, question, first_ms, total_ms, bool(tools), text.startswith("ERROR")))
                print(f"\n👤 {question}")
                print(f"🤖 {text}")
                print(f"   ⏱ first words {(first_ms or total_ms) / 1000:.1f}s | total {total_ms / 1000:.1f}s"
                      f" | tools: {', '.join(tools) or '-'}")

    def avg(values):
        values = [v / 1000 for v in values if v is not None]
        return f"{sum(values) / len(values):5.1f}s" if values else "    -"

    print(f"\n{'=' * 78}\nSUMMARY ({len(rows)} replies)\n{'=' * 78}")
    for label, group in [("all replies", rows),
                         ("with a tool", [r for r in rows if r[4]]),
                         ("without a tool", [r for r in rows if not r[4]])]:
        print(f"{label:16} {len(group):3} | avg first words {avg([r[2] or r[3] for r in group])}"
              f" | avg total {avg([r[3] for r in group])}")
    slowest = max(rows, key=lambda r: r[2] or r[3])
    print(f"slowest first words: {(slowest[2] or slowest[3]) / 1000:.1f}s  \"{slowest[1]}\"")
    print(f"errors: {sum(r[5] for r in rows)}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
