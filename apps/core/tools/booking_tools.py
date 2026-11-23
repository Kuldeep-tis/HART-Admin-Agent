"""Tools for live booking data (hotel_info.db: bookings). Never return guest personal data."""

from datetime import date, timedelta

from apps.core.services import booking_service
from apps.core.tools.registry import (PERIOD, ToolInputError, check_range, date_range, parse_date,
                                      resolve_period, tool)

DATE = {"type": "string", "description": "YYYY-MM-DD"}
STATUSES = ["confirmed", "checked_in", "checked_out", "cancelled"]


@tool(
    "check_room_availability",
    label="Checked room availability",
    progress="Checking room availability…",
    description=(
        "Free rooms per type for a stay (check_out defaults to the day after check_in). "
        "For 'is a room available', 'rooms free tonight'."
    ),
    parameters={
        "type": "object",
        "properties": {"check_in": DATE, "check_out": DATE},
        "required": ["check_in"],
    },
)
def check_room_availability(hotel_id, check_in=None, check_out=None):
    start = parse_date(check_in, field="check_in")
    end = parse_date(check_out, default=start + timedelta(days=1), field="check_out")
    check_range(start, end, "check_in", "check_out", allow_equal=False)
    rooms = booking_service.room_availability(hotel_id, start.isoformat(), end.isoformat())
    return {
        "check_in": start.isoformat(), "check_out": end.isoformat(),
        "total_available_rooms": sum(r["available_rooms"] for r in rooms),
        "room_types": rooms,
    }


@tool(
    "get_occupancy",
    label="Checked occupancy and check-ins",
    progress="Checking occupancy…",
    description=(
        "One night's occupancy: rooms occupied, guests, occupancy %, arrivals, departures, "
        "currently checked in. date defaults to today."
    ),
    parameters={"type": "object", "properties": {"date": DATE}},
)
def get_occupancy(hotel_id, date=None):
    day = parse_date(date, default=_today(), field="date")
    return booking_service.occupancy(hotel_id, day.isoformat())


@tool(
    "get_booking_summary",
    label="Summarised bookings",
    progress="Adding up bookings…",
    description=(
        "Totals (sales, revenue in INR, counts by status, guests, cancellation rate, per room type) "
        "for bookings checking in during a period, or between start_date and end_date."
    ),
    parameters={
        "type": "object",
        "properties": {"period": PERIOD, "start_date": DATE, "end_date": DATE},
    },
)
def get_booking_summary(hotel_id, period=None, start_date=None, end_date=None):
    start, end = date_range(period, start_date, end_date, default_period="this_month")
    return booking_service.booking_summary(hotel_id, start.isoformat(), end.isoformat())


# Five narrow columns so the table fits the chat panel (~370px). A "\n" in a cell starts a
# smaller second line in the chat window (e.g. the booking number under the room).
BOOKING_COLUMNS = ["Booking", "Stay", "Guests", "Amount", "Status"]


def _bookings_title(result):
    total = result["matching_bookings"]
    title = f"{total} booking{'s' if total != 1 else ''} found"
    if result["showing"] < total:
        title += f", showing the newest {result['showing']}"
    return title


def _stay(check_in, check_out):
    """Short dates: '18 → 20 Nov', '30 Oct → 2 Nov', plus the year only when it isn't this year."""
    start = f"{check_in.day}" if (check_in.month, check_in.year) == (check_out.month, check_out.year) \
        else f"{check_in.day} {check_in:%b}"
    end = f"{check_out.day} {check_out:%b}" + (f" {check_out.year}" if check_out.year != _today().year else "")
    return f"{start} → {end}"


def _booking_view(b):
    """One booking's display values. Text and table both use these, so they always agree."""
    check_in, check_out = (date.fromisoformat(b[k]) for k in ("check_in_date", "check_out_date"))
    return {
        "id": f"#{b['booking_id']}", "room": b["room_type"],
        "stay": _stay(check_in, check_out), "nights": f"{b['nights']} night{'s' if b['nights'] != 1 else ''}",
        "guests": str(b["num_guests"]), "amount": f"₹{b['total_amount']:,.0f}",
        "status": b["status"].replace("_", " "),
    }


def _render_bookings(result):
    """list_bookings result -> the finished answer as plain text (stored, and shown in the transcript)."""
    if not result["bookings"]:
        return "No bookings match that."
    lines = [_bookings_title(result) + ":"]
    for v in map(_booking_view, result["bookings"]):
        lines.append(f"• {v['id']} · {v['room']} · {v['stay']} ({v['nights']}) · {v['guests']} guests"
                     f" · {v['amount']} · {v['status']}")
    return "\n".join(lines)


def _table_bookings(result):
    """list_bookings result -> rows and columns for the chat window (None when there is nothing to list)."""
    if not result["bookings"]:
        return None
    rows = [[f"{v['room']}\n{v['id']}", f"{v['stay']}\n{v['nights']}", v["guests"], v["amount"], v["status"]]
            for v in map(_booking_view, result["bookings"])]
    return {"title": _bookings_title(result), "columns": BOOKING_COLUMNS, "rows": rows}


@tool(
    "list_bookings",
    label="Listed bookings",
    progress="Finding bookings…",
    description=(
        "List bookings (id, room type, dates, guests, amount, status), newest first. "
        "Filters: status, and check-in period or date range. No guest names."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": STATUSES},
            "period": PERIOD,
            "start_date": DATE,
            "end_date": DATE,
            "limit": {"type": "integer", "description": "1-20, default 10"},
        },
    },
    render=_render_bookings,
    table=_table_bookings,
)
def list_bookings(hotel_id, status=None, period=None, start_date=None, end_date=None, limit=10):
    if status and status not in STATUSES:
        raise ToolInputError(f"status must be one of {', '.join(STATUSES)}.")
    if period:
        start, end = resolve_period(period)
    else:   # no period: each date is an optional filter on its own
        start = parse_date(start_date, field="start_date") if start_date else None
        end = parse_date(end_date, field="end_date") if end_date else None
    if start and end:
        check_range(start, end)
    try:
        limit = max(1, min(int(limit), 20))
    except (TypeError, ValueError):
        limit = 10
    return booking_service.list_bookings(
        hotel_id, status=status,
        start_date=start.isoformat() if start else None,
        end_date=end.isoformat() if end else None,
        limit=limit,
    )


def _today():
    return date.today()
