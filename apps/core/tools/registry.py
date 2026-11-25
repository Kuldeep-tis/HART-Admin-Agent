"""Tool registry: the list of database tools the AI may call.

A tool = a Python function + a description + a JSON schema of its arguments.
Gemini only sees the description and schema; it asks us to run a tool, we run it.

SECURITY: hotel_id is NOT a tool argument. execute() always passes the agent's
own hotel_id, so the AI cannot read another hotel's data.
"""

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable

from google.genai import types

MAX_RANGE_DAYS = 366


class ToolInputError(ValueError):
    """Bad arguments from the AI. Sent back to it so it can try again."""


@dataclass
class Tool:
    name: str
    label: str            # short human text, shown in the chat ("Checked room availability")
    description: str      # tells the AI WHEN to use the tool
    parameters: dict      # JSON schema of the arguments
    fn: Callable          # fn(hotel_id, **args) -> dict
    render: Callable | None = None   # render(result) -> str: the finished answer, written by Python
    table: Callable | None = None    # table(result) -> {"title", "columns", "rows"} | None: same answer as a table
    progress: str = "Looking up hotel data…"   # shown in the chat WHILE the tool runs


TOOLS: dict[str, Tool] = {}


def tool(name, label, description, parameters=None, render=None, table=None,
         progress="Looking up hotel data…"):
    """Decorator that registers a function as a tool.

    render: optional. For tools whose result is a list, Python writes the answer itself:
    instant and always exact, instead of a slow local model copying every row (~6 words/s on CPU).
    table: optional. The same answer as rows and columns, for the chat window to draw as a table.
    """
    def register(fn):
        TOOLS[name] = Tool(name, label, description, parameters or {"type": "object", "properties": {}},
                           fn, render, table, progress)
        return fn
    return register


def render(name, result):
    """The finished answer for this tool result, or None when the AI should write it
    (the tool has no renderer, or the result is an error the AI should explain or fix)."""
    t = TOOLS.get(name)
    if t is None or t.render is None or "error" in result:
        return None
    return t.render(result)


def table(name, result):
    """The answer as a table for the chat window, or None (no table for this tool or result)."""
    t = TOOLS.get(name)
    if t is None or t.table is None or "error" in result:
        return None
    return t.table(result)


def declarations():
    """Tool definitions in Gemini's format."""
    return [
        types.Tool(function_declarations=[
            types.FunctionDeclaration(name=t.name, description=t.description, parameters_json_schema=t.parameters)
            for t in TOOLS.values()
        ])
    ]


def execute(name, args, hotel_id):
    """Run a tool for this hotel. Always returns a JSON-safe dict (errors included)."""
    t = TOOLS.get(name)
    if t is None:
        return {"error": f"Unknown tool '{name}'."}
    allowed = set(t.parameters.get("properties", {}))
    clean = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    clean.pop("hotel_id", None)   # SECURITY: never from the AI. Dropped silently; we pass our own below.
    unknown = sorted(set(clean) - allowed)
    if unknown:
        # Don't silently drop it: the AI would think it filtered (e.g. by booking id) when it didn't.
        # The error goes back to the AI, which retries with the right arguments.
        return {"error": f"{name} has no argument {', '.join(unknown)}. "
                         f"Allowed: {', '.join(sorted(allowed)) or 'none'}."}
    try:
        result = t.fn(hotel_id, **clean)
    except ToolInputError as exc:
        return {"error": str(exc)}
    return result if isinstance(result, dict) else {"result": result}


# ---------- argument helpers ----------

def parse_date(value, default=None, field="date"):
    """'YYYY-MM-DD' -> date. Uses default when value is empty."""
    if not value:
        if default is None:
            raise ToolInputError(f"{field} is required (format YYYY-MM-DD).")
        return default
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ToolInputError(f"{field} must be a date in YYYY-MM-DD format, got '{value}'.") from None


# Named periods, so the AI picks a word and Python does the date maths. Small models get
# "last month" or "this month so far" wrong when they compute dates themselves.
PERIODS = ["today", "yesterday", "this_week", "last_week", "this_month", "last_month",
           "this_year", "last_7_days", "last_30_days", "next_7_days", "next_30_days"]
PERIOD = {
    "type": "string", "enum": PERIODS,
    "description": "Use instead of dates when it fits. this_week/this_month/this_year end today. "
                   "next_7_days/next_30_days start today (upcoming).",
}


# Period words in the staff member's question -> our period names. Longest phrases first.
PERIOD_PHRASES = [
    (r"(?:last|past|previous) 30 days", "last_30_days"), (r"(?:last|past|previous) 7 days", "last_7_days"),
    (r"next 30 days|next month|coming month", "next_30_days"), (r"next 7 days|next week|coming week", "next_7_days"),
    (r"(?:last|previous) month", "last_month"), (r"this month", "this_month"),
    (r"(?:last|previous) week", "last_week"), (r"this week", "this_week"),
    (r"this year", "this_year"), (r"yesterday", "yesterday"), (r"today", "today"),
]


def period_in(text):
    """The one period a question names ("and last month?" -> "last_month"), else None.
    None also when it names two ("this month vs last month"): then the AI must decide."""
    text, found = (text or "").lower(), set()
    for pattern, period in PERIOD_PHRASES:
        if re.search(rf"\b(?:{pattern})\b", text):
            found.add(period)
            text = re.sub(rf"\b(?:{pattern})\b", " ", text)   # "last 30 days" is not also "30 days"
    return found.pop() if len(found) == 1 else None


def steer_period(name, args, question):
    """Use the period the staff member actually named, instead of the AI's guess.

    A small model answering a follow-up ("and last month?") copies the earlier call
    (period=this_month, plus an end date). Python reads the question, so the dates are right.
    Only for tools that take a period, and only when the question names exactly one.
    """
    t = TOOLS.get(name)
    wanted = period_in(question)
    if t is None or wanted is None or "period" not in t.parameters.get("properties", {}):
        return args
    args = {k: v for k, v in (args or {}).items() if k not in ("start_date", "end_date")}
    return {**args, "period": wanted}


def resolve_period(period, today=None):
    """'last_month' -> (date(2026, 10, 1), date(2026, 10, 31)). Periods named 'this_...' end today."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    first_of_month = today.replace(day=1)
    end_of_last_month = first_of_month - timedelta(days=1)
    ranges = {
        "today": (today, today),
        "yesterday": (today - timedelta(days=1),) * 2,
        "this_week": (monday, today),
        "last_week": (monday - timedelta(days=7), monday - timedelta(days=1)),
        "this_month": (first_of_month, today),
        "last_month": (end_of_last_month.replace(day=1), end_of_last_month),
        "this_year": (today.replace(month=1, day=1), today),
        "last_7_days": (today - timedelta(days=6), today),
        "last_30_days": (today - timedelta(days=29), today),
        "next_7_days": (today, today + timedelta(days=6)),
        "next_30_days": (today, today + timedelta(days=29)),
    }
    if period not in ranges:
        raise ToolInputError(f"period must be one of {', '.join(PERIODS)}.")
    return ranges[period]


def check_period_or_dates(period, start_date, end_date):
    """Both a period and dates is ambiguous ("this_month" + end 30 Dec: which one?). Picking one
    silently gave wrong answers, so the AI is told to choose and retries."""
    if period and (start_date or end_date):
        raise ToolInputError("Send either period or start_date/end_date, not both. Call the tool again "
                             "with only a period (e.g. last_month, this_month, next_30_days) or only dates.")


def date_range(period, start_date, end_date, default_period=None):
    """The (start, end) dates a tool should use: a named period, or explicit dates."""
    check_period_or_dates(period, start_date, end_date)
    if period:
        return resolve_period(period)
    if start_date or end_date or default_period is None:
        start = parse_date(start_date, field="start_date")
        end = parse_date(end_date, field="end_date")
        check_range(start, end)
        return start, end
    return resolve_period(default_period)


def check_range(start, end, start_name="start_date", end_name="end_date", allow_equal=True):
    if end < start or (end == start and not allow_equal):
        raise ToolInputError(f"{end_name} must be after {start_name}.")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise ToolInputError(f"The date range can be at most {MAX_RANGE_DAYS} days.")
