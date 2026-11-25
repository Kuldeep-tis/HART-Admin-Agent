"""Talk to an agent like real hotel staff would, time every reply and check every answer.

Each scenario below is a NEW chat (a new session), with follow-up questions, so the
agent sees its own earlier answers just like in the real chat window. Every chat is
saved like a real preview chat, so you can review it later in the dashboard:
    Dashboard -> agent -> Conversations -> (open a chat) -> replies with "first words" times.

Usage (Ollama running, LLM_PROVIDER=local, the model already pulled):
    python scripts/chat_test.py              # agent 1, all scenarios
    python scripts/chat_test.py 2            # another agent id

Every answer is checked against the database (see build_checks): ✅ right, ❌ wrong.
The expected answers are read when the script starts, so they stay right as dates change.

Close heavy apps (browser) first: on an 8 GB machine, swapping makes every reply slow.
"""

import re
import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from apps.core import agent as hotel_agent  # noqa: E402
from apps.core.services import agent_service, conversation_service, hotel_service  # noqa: E402
from apps.core.tools import booking_tools, hotel_tools  # noqa: E402
from helpers.database import get_hotel_db, get_platform_db  # noqa: E402

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


# ---------------------------------------------------------------- answer checks
# Answers are lower-cased and commas removed before checking, so "₹1,50,000" matches "150000".

NOT_KNOWN = ("don't have", "do not have", "no information", "not available", "not sure",
             "unable to", "not mentioned", "not listed", "doesn't", "does not")
DECLINE = NOT_KNOWN + ("only help", "can't help", "cannot help", "can only", "not able")


def number(n):
    """85500.0 -> "85500", as the model may write it."""
    return str(int(n)) if float(n).is_integer() else str(n)


def has_word(word):
    """True if `word` appears on its own, so "2" doesn't match inside "2026"."""
    return lambda text: re.search(rf"(?<![\d.]){re.escape(word)}(?![\d])", text) is not None


def has_any(*parts):
    return lambda text: any(p in text for p in parts)


def both(*checks):
    return lambda text: all(c(text) for c in checks)


def build_checks(hotel_id):
    """{question: (check(answer) -> bool, what a right answer contains)}, read from the database now."""
    today = date.today()
    tomorrow = today + timedelta(days=1)
    first_this_month = today.replace(day=1)
    last_month_end = first_this_month - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)
    saturday = today + timedelta(days=(5 - today.weekday()) % 7)   # this weekend's Saturday night

    last_month = booking_tools.get_booking_summary(
        hotel_id, start_date=last_month_start.isoformat(), end_date=last_month_end.isoformat())
    this_month = booking_tools.get_booking_summary(
        hotel_id, start_date=first_this_month.isoformat(), end_date=today.isoformat())
    cancelled = booking_tools.list_bookings(hotel_id, status="cancelled")["bookings"]
    occ_today = booking_tools.get_occupancy(hotel_id, today.isoformat())
    free_tomorrow = booking_tools.check_room_availability(
        hotel_id, tomorrow.isoformat(), (tomorrow + timedelta(days=1)).isoformat())
    free_per_type = [str(r["available_rooms"]) for r in free_tomorrow["room_types"]]
    sea_view = booking_tools.check_room_availability(
        hotel_id, saturday.isoformat(), (saturday + timedelta(days=1)).isoformat(), room_type="Sea View")
    sea_view_free = sum(r["available_rooms"] for r in sea_view["room_types"])
    upcoming = booking_tools.list_bookings(
        hotel_id, status="confirmed", start_date=today.isoformat(),
        end_date=(today + timedelta(days=30)).isoformat(), limit=20)["bookings"]
    info = hotel_tools.get_hotel_info(hotel_id)
    checkout = info["check_out_time"]                      # "10:00"
    checkout_hour = str(int(checkout.split(":")[0]))       # "10"

    # Booking 46: guest names are private (prompts.py), so a right answer gives the booking's
    # details and does NOT show the name.
    b46 = booking_tools.list_bookings(hotel_id, booking_id=46)["bookings"]
    name_row = get_hotel_db().execute(
        "SELECT guest_name FROM bookings WHERE id = 46 AND hotel_id = ?", (hotel_id,)).fetchone()
    guest_name = (name_row["guest_name"] if name_row else "").lower()
    b46_detail = b46[0]["room_type"].lower() if b46 else None

    arrivals, departures = occ_today["arrivals"], occ_today["departures"]
    if arrivals or departures:
        today_check = both(*(has_word(str(n)) for n in (arrivals, departures) if n))
        today_meaning = f"{arrivals} arrivals, {departures} departures"
    else:
        today_check = has_any("no ", "none", "0 ")
        today_meaning = "none today"

    return {
        "hi": (lambda t: not re.search(r"\d", t), "a greeting, no numbers"),
        "what were last month's sales?": (
            has_word(number(last_month["revenue"])), f"₹{number(last_month['revenue'])}"),
        "and this month so far?": (
            has_word(number(this_month["revenue"])), f"₹{number(this_month['revenue'])}"),
        "how many bookings were cancelled last month?": (
            has_word(str(last_month["cancelled"])), f"{last_month['cancelled']} cancelled"),
        "show the cancelled bookings": (
            both(*(has_word(str(b["booking_id"])) for b in cancelled)) if cancelled else has_any(*NOT_KNOWN, "no "),
            "bookings " + ", ".join(f"#{b['booking_id']}" for b in cancelled) if cancelled else "says none"),
        "which guest made booking 46?": (
            (lambda t: (not guest_name or guest_name not in t) and b46_detail in t) if b46_detail
            else has_any(*NOT_KNOWN, "not found", "no booking"),
            f"no guest name, but the details ({b46_detail})" if b46_detail else "booking not found"),
        "show upcoming confirmed bookings for the next 30 days": (
            both(*(has_word(str(b["booking_id"])) for b in upcoming[:10])) if upcoming else has_any("no ", "none"),
            f"{len(upcoming)} bookings, incl. " + ", ".join(f"#{b['booking_id']}" for b in upcoming[:3])
            if upcoming else "none"),
        "how many guests are checked in right now?": (
            has_word(str(occ_today["checked_in_guests"])), f"{occ_today['checked_in_guests']} guests"),
        "any arrivals or departures today?": (today_check, today_meaning),
        "how many rooms are free tomorrow?": (
            lambda t: has_word(str(free_tomorrow["total_available_rooms"]))(t)
            or all(has_word(n)(t) for n in free_per_type),
            f"{free_tomorrow['total_available_rooms']} rooms"),
        "is a sea view room free this weekend?": (
            has_word(str(sea_view_free)) if sea_view_free else has_any("no ", "not available", "fully"),
            f"{sea_view_free} Sea View rooms free on {saturday.isoformat()}"),
        "what time is check-out?": (
            lambda t: has_word(checkout)(t) or bool(re.search(rf"(?<!\d){checkout_hour}\s*(?::00)?\s*a\.?m", t)),
            checkout),
        "can guests bring a dog?": (has_word("1000"), "yes, fee ₹1000 per night"),
        "do we have a gym?": (lambda t: has_any(*NOT_KNOWN)(t) and not t.startswith("yes"), "doesn't invent a gym"),
        "what's the weather in goa today?": (has_any(*DECLINE), "declines (not hotel data)"),
    }


def grade(checks, question, text):
    """(right?, what a right answer contains). Unknown questions are not graded (None)."""
    check, meaning = checks.get(question.lower(), (None, "not checked"))
    if check is None:
        return None, meaning
    return (not text.startswith("ERROR") and check(text.lower().replace(",", ""))), meaning


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

        checks = build_checks(agent["hotel_id"])
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
                right, meaning = grade(checks, question, text)
                rows.append((title, question, first_ms, total_ms, bool(tools), text.startswith("ERROR"), right))
                mark = {True: "✅", False: "❌", None: "  "}[right]
                print(f"\n{mark} 👤 {question}")
                print(f"🤖 {text}")
                if right is False:
                    print(f"   ✗ expected: {meaning}")
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
    graded = [r for r in rows if r[6] is not None]
    print(f"\nRIGHT ANSWERS: {sum(r[6] for r in graded)}/{len(graded)}")
    for r in graded:
        if not r[6]:
            print(f"  ❌ [{r[0]}] {r[1]}")


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 1)
