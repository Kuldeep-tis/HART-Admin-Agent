"""Compare local Ollama models on real staff questions, through our real agent code.

For each model it asks the same questions (real system prompt, real tools, real
hotel_info.db) and measures:
  - speed:    time to first words and total time
  - tool:     did it call the right database tool?
  - correct:  does the answer contain the facts from the database?
  - invented: did it make anything up (a pool, a fake phone number, "XXXX")?
  - RAM:      how much memory the loaded model uses

The expected answers are read from the database when the script starts, so the
checks stay right when the sample data or today's date changes.

Usage (Ollama must be running, and nothing else should be using it meanwhile):
    python scripts/benchmark_models.py                          # default models
    python scripts/benchmark_models.py granite4:3b qwen3.5:4b   # chosen models

Close heavy apps (browser) first: on an 8 GB machine, swapping makes every model slow.
"""

import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from apps.core import agent as hotel_agent  # noqa: E402
from apps.core.services import agent_service  # noqa: E402
from apps.core.tools import booking_tools, hotel_tools  # noqa: E402

DEFAULT_MODELS = ["qwen2.5:3b", "granite4:3b"]   # the winner + runner-up of the 2026-11-23 benchmark
HOTEL_ID = 2   # Seaside Retreat (sample data from database/seed_main_db.py)

NOT_KNOWN = ("don't have", "do not have", "no information", "not available", "not sure",
             "unable to", "not mentioned", "not listed", "doesn't", "does not")


def number(n):
    """A number as the model may write it: 85500.0 -> "85500" (commas are removed before checking)."""
    return str(int(n)) if float(n).is_integer() else str(n)


def has_word(word):
    """True if `word` appears on its own, so "2" doesn't match inside "2026"."""
    return lambda text: re.search(rf"(?<![\d.]){re.escape(word)}(?![\d])", text) is not None


def has_all(*parts):
    return lambda text: all(p in text for p in parts)


def has_any(*parts):
    return lambda text: any(p in text for p in parts)


def build_questions():
    """(question, expected tool or None, check on the answer, what the check means).
    Answers are lower-cased and commas removed first, so "₹4,500" matches "4500"."""
    today = date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    rooms = hotel_tools.get_room_types(HOTEL_ID)["room_types"]
    availability = booking_tools.check_room_availability(HOTEL_ID, tomorrow)
    free = availability["total_available_rooms"]
    free_per_type = [str(r["available_rooms"]) for r in availability["room_types"]]
    occ = booking_tools.get_occupancy(HOTEL_ID)
    month = booking_tools.get_booking_summary(
        HOTEL_ID, start_date=today.replace(day=1).isoformat(), end_date=today.isoformat())
    cancelled = booking_tools.list_bookings(HOTEL_ID, status="cancelled")["bookings"]
    prices = [number(r["price_per_night"]) for r in rooms]

    return [
        ("What time is check-in?", "get_hotel_info",   # has_word: "12:00" must not pass as "2:00"
         lambda t: any(has_word(w)(t) for w in ("14:00", "2:00", "2 pm", "2pm")), "14:00 / 2 PM"),
        ("What room types do we have and what do they cost?", "get_room_types",
         has_all(*prices), "all prices: " + ", ".join(prices)),
        ("Can guests bring a dog?", "get_policies",
         has_word("1000"), "pets allowed, fee 1000"),
        ("What is our cancellation policy?", "get_policies",
         has_any("7 days", "seven days"), "non-refundable within 7 days"),
        ("How many rooms are free tomorrow?", "check_room_availability",
         # the total, or every per-type count (correct facts, just not added up)
         lambda t: has_word(str(free))(t) or all(has_word(n)(t) for n in free_per_type),
         f"{free} rooms free"),
        ("How many guests are checked in right now?", "get_occupancy",
         has_word(str(occ["checked_in_guests"])), f"{occ['checked_in_guests']} guests"),
        ("What is our revenue this month so far?", "get_booking_summary",
         has_word(number(month["revenue"])), f"₹{number(month['revenue'])}"),
        ("Show the cancelled bookings.", "list_bookings",
         has_word(str(cancelled[0]["booking_id"])) if cancelled else has_any(*NOT_KNOWN, "no "),
         f"includes booking {cancelled[0]['booking_id']}" if cancelled else "says none"),
        ("Do we have a swimming pool?", None,
         lambda t: has_any(*NOT_KNOWN)(t) and not t.startswith("yes"), "doesn't invent a pool"),
    ]


def invented_contact(text, info):
    """Return what was made up: placeholders, or a phone/email that isn't the hotel's real one."""
    if "xxx" in text.lower():
        return "placeholder XXXX"
    real_phone = re.sub(r"\D", "", info["phone"])
    for phone in re.findall(r"\+?\d[\d\s-]{8,}\d", text):
        if re.search(r"\d{4}-\d{2}-\d{2}", phone):
            continue   # a date like 2026-10-30, not a phone number
        if re.sub(r"\D", "", phone) != real_phone:
            return f"fake phone {phone.strip()}"
    for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.]+", text):
        if email.lower().rstrip(".") != info["email"].lower():
            return f"fake email {email}"
    return None


def unload(url, model):
    """Free the model's RAM before loading the next one (only one fits on 8 GB)."""
    try:
        requests.post(f"{url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)
    except requests.RequestException:
        pass


def ram_gb(url, model):
    """How much memory Ollama reports for the loaded model (weights + working memory)."""
    try:
        for m in requests.get(f"{url}/api/ps", timeout=10).json().get("models", []):
            if m["name"] == model or m["model"] == model:
                return m["size"] / 1024 ** 3
    except (requests.RequestException, ValueError, KeyError):
        pass
    return 0.0


def ask(agent, question):
    started, first = time.perf_counter(), None
    try:
        for event in hotel_agent.respond_stream(agent, question, []):
            if event["type"] in ("token", "table") and first is None:
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
        questions = build_questions()
        info = hotel_tools.get_hotel_info(HOTEL_ID)

        for model in models:
            app.config["OLLAMA_MODEL"] = model
            print(f"\n{'=' * 70}\n{model}\n{'=' * 70}", flush=True)
            for m in models:
                if m != model:
                    unload(url, m)

            t = time.perf_counter()
            ask(agent, "hi")   # load the model + read prompt/tools once (not counted)
            ram = ram_gb(url, model)
            print(f"  (loaded and warmed in {time.perf_counter() - t:.0f}s, RAM {ram:.1f} GB)", flush=True)

            rows = []
            for question, want_tool, check, meaning in questions:
                text, used, first, total = ask(agent, question)
                tool_ok = (want_tool in used) if want_tool else True
                fake = invented_contact(text, info)
                answer_ok = not text.startswith("ERROR") and not fake and check(text.lower().replace(",", ""))
                rows.append((tool_ok, answer_ok, first, total))
                print(f"  {'✅' if tool_ok and answer_ok else '❌'} {question}\n"
                      f"     tool: {', '.join(used) or '-'} ({'ok' if tool_ok else 'expected ' + str(want_tool)})"
                      f" | answer {'ok' if answer_ok else 'WRONG'} ({fake or meaning})"
                      f" | first words {first or 0:.1f}s, total {total:.1f}s\n"
                      f"     → {text[:200]}", flush=True)

            n = len(rows)
            summary.append((
                model, ram,
                sum(r[0] for r in rows), sum(r[1] for r in rows), n,
                sum((r[2] or r[3]) for r in rows) / n, sum(r[3] for r in rows) / n,
            ))
            unload(url, model)

    print(f"\n{'=' * 70}\nSUMMARY ({len(questions)} questions, Seaside Retreat)\n{'=' * 70}")
    print(f"{'model':16} {'RAM':>6} {'right tool':>11} {'right answer':>13} {'avg first words':>16} {'avg total':>10}")
    for model, ram, tools_ok, answers_ok, n, avg_first, avg_total in summary:
        print(f"{model:16} {ram:>4.1f}GB {tools_ok:>8}/{n} {answers_ok:>10}/{n} "
              f"{avg_first:>15.1f}s {avg_total:>9.1f}s")


if __name__ == "__main__":
    run(sys.argv[1:] or DEFAULT_MODELS)
