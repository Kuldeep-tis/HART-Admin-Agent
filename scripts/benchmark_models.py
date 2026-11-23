"""Compare local Ollama models on real hotel questions, through our real agent code.

For each model it asks the same questions (real system prompt, real tools, real
hotel_info.db) and measures:
  - speed:    time to first words and total time
  - tool:     did it call the right database tool?
  - correct:  does the answer contain the facts from the database?

Usage (Ollama must be running, and nothing else should be using it meanwhile):
    python scripts/benchmark_models.py                          # default models
    python scripts/benchmark_models.py granite4:3b qwen3.5:4b   # chosen models

Close heavy apps (browser) first: on an 8 GB machine, swapping makes every model slow.
"""

import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from apps.core import agent as hotel_agent  # noqa: E402
from apps.core.services import agent_service  # noqa: E402

DEFAULT_MODELS = ["qwen3.5:4b", "granite4:3b", "phi4-mini", "qwen3.5:2b"]
HOTEL_ID = 2   # Seaside Retreat (sample data from database/seed_main_db.py)


def has_all(*parts):
    return lambda text: all(p in text for p in parts)


def has_any(*parts):
    return lambda text: any(p in text for p in parts)


# (question, expected tool or None, check on the answer, what the check means)
# Answers are lower-cased and commas removed first, so "₹4,500" matches "4500".
QUESTIONS = [
    ("What time is check-in?", "get_hotel_info",
     has_any("14:00", "2:00", "2 pm", "2pm"), "says 14:00 / 2 PM"),
    ("What room types do you have and what do they cost?", "get_room_types",
     has_all("4500", "7000", "15000"), "all 3 prices"),
    ("Can I bring my dog?", "get_policies",
     has_all("1000"), "pets allowed, fee 1,000/night"),
    ("What is your cancellation policy?", "get_policies",
     has_any("7 days", "seven days", "non-refundable"), "non-refundable within 7 days"),
    ("Are rooms available tomorrow?", "check_room_availability",
     has_any("44", "available"), "44 rooms free"),
    ("Do you have a swimming pool?", None,
     has_any("don't have", "do not have", "not available", "no information", "not sure",
             "unable", "not mentioned", "doesn't", "does not", "not listed", "contact"),
     "doesn't invent a pool"),
]


def unload(url, model):
    """Free the model's RAM before loading the next one (only one fits on 8 GB)."""
    try:
        requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except requests.RequestException:
        pass


def ask(agent, question):
    started, first = time.perf_counter(), None
    try:
        for event in hotel_agent.respond_stream(agent, question, []):
            if event["type"] == "token" and first is None:
                first = time.perf_counter() - started
            if event["type"] == "final":
                reply = event["reply"]
                return reply.text, [c["name"] for c in reply.tool_calls], first, time.perf_counter() - started
    except hotel_agent.AgentError as exc:
        return f"ERROR: {exc}", [], first, time.perf_counter() - started
    return "ERROR: no reply", [], first, time.perf_counter() - started


def run(models):
    app = create_app()
    app.config["LLM_PROVIDER"] = "local"
    url = app.config["OLLAMA_URL"]
    summary = []

    with app.app_context():
        agent = next(r["agent"] for r in agent_service.list_agents_with_hotels()
                     if r["agent"]["hotel_id"] == HOTEL_ID)

        for model in models:
            app.config["OLLAMA_MODEL"] = model
            print(f"\n{'=' * 70}\n{model}\n{'=' * 70}", flush=True)
            for m in models:
                if m != model:
                    unload(url, m)

            t = time.perf_counter()
            ask(agent, "hi")   # load the model + read prompt/tools once (not counted)
            print(f"  (loaded and warmed in {time.perf_counter() - t:.0f}s)", flush=True)

            rows = []
            for question, want_tool, check, meaning in QUESTIONS:
                text, used, first, total = ask(agent, question)
                tool_ok = (want_tool in used) if want_tool else True
                answer_ok = not text.startswith("ERROR") and check(text.lower().replace(",", ""))
                rows.append((tool_ok, answer_ok, first, total))
                print(f"  {'✅' if tool_ok and answer_ok else '❌'} {question}\n"
                      f"     tool: {', '.join(used) or '-'} ({'ok' if tool_ok else 'expected ' + str(want_tool)})"
                      f" | answer {'ok' if answer_ok else 'WRONG'} ({meaning})"
                      f" | first words {first or 0:.1f}s, total {total:.1f}s\n"
                      f"     → {text[:160]}", flush=True)

            n = len(rows)
            summary.append((
                model,
                sum(r[0] for r in rows), sum(r[1] for r in rows), n,
                sum((r[2] or r[3]) for r in rows) / n, sum(r[3] for r in rows) / n,
            ))
            unload(url, model)

    print(f"\n{'=' * 70}\nSUMMARY ({len(QUESTIONS)} questions, Seaside Retreat)\n{'=' * 70}")
    print(f"{'model':16} {'right tool':>11} {'right answer':>13} {'avg first words':>16} {'avg total':>10}")
    for model, tools_ok, answers_ok, n, avg_first, avg_total in summary:
        print(f"{model:16} {tools_ok:>8}/{n} {answers_ok:>10}/{n} {avg_first:>15.1f}s {avg_total:>9.1f}s")


if __name__ == "__main__":
    run(sys.argv[1:] or DEFAULT_MODELS)
