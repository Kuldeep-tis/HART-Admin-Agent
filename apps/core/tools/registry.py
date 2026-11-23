"""Tool registry: the list of database tools the AI may call.

A tool = a Python function + a description + a JSON schema of its arguments.
Gemini only sees the description and schema; it asks us to run a tool, we run it.

SECURITY: hotel_id is NOT a tool argument. execute() always passes the agent's
own hotel_id, so the AI cannot read another hotel's data.
"""

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
    clean = {k: v for k, v in (args or {}).items() if k in allowed and v not in (None, "")}
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
    "description": "Use instead of dates when it fits. this_week/this_month/this_year end today.",
}


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


def date_range(period, start_date, end_date, default_period=None):
    """The (start, end) dates a tool should use: a named period wins over explicit dates."""
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
