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


TOOLS: dict[str, Tool] = {}


def tool(name, label, description, parameters=None):
    """Decorator that registers a function as a tool."""
    def register(fn):
        TOOLS[name] = Tool(name, label, description, parameters or {"type": "object", "properties": {}}, fn)
        return fn
    return register


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


def check_range(start, end, start_name="start_date", end_name="end_date", allow_equal=True):
    if end < start or (end == start and not allow_equal):
        raise ToolInputError(f"{end_name} must be after {start_name}.")
    if (end - start) > timedelta(days=MAX_RANGE_DAYS):
        raise ToolInputError(f"The date range can be at most {MAX_RANGE_DAYS} days.")
