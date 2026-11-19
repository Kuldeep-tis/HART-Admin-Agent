"""Tools for live booking data (hotel_info.db: bookings). Never return guest personal data."""

from datetime import date, timedelta

from apps.core.services import booking_service
from apps.core.tools.registry import ToolInputError, check_range, parse_date, tool

DATE = {"type": "string", "description": "Date in YYYY-MM-DD format."}
STATUSES = ["confirmed", "checked_in", "checked_out", "cancelled"]


@tool(
    "check_room_availability",
    label="Checked room availability",
    description=(
        "Check how many rooms of each type are free for a stay. check_in is the arrival date, "
        "check_out the departure date (defaults to the day after check_in). "
        "Use for 'is a room available', 'how many rooms are free tonight/next weekend'."
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
    description=(
        "Get occupancy for one night: rooms occupied, guests staying, occupancy percent, arrivals and "
        "departures that day, plus how many bookings and guests are currently checked in. "
        "Use for 'how many guests are in the hotel', 'how many checked in', 'occupancy rate', 'arrivals today'. "
        "date defaults to today."
    ),
    parameters={"type": "object", "properties": {"date": DATE}},
)
def get_occupancy(hotel_id, date=None):
    day = parse_date(date, default=_today(), field="date")
    return booking_service.occupancy(hotel_id, day.isoformat())


@tool(
    "get_booking_summary",
    label="Summarised bookings",
    description=(
        "Summarise bookings whose check-in date is between start_date and end_date (inclusive): totals by "
        "status, revenue (INR, excluding cancellations), guests, average nights, cancellation rate and a "
        "breakdown per room type. Use for 'bookings this month', 'revenue last week', 'cancellation rate'."
    ),
    parameters={
        "type": "object",
        "properties": {"start_date": DATE, "end_date": DATE},
        "required": ["start_date", "end_date"],
    },
)
def get_booking_summary(hotel_id, start_date=None, end_date=None):
    start = parse_date(start_date, field="start_date")
    end = parse_date(end_date, field="end_date")
    check_range(start, end)
    return booking_service.booking_summary(hotel_id, start.isoformat(), end.isoformat())


@tool(
    "list_bookings",
    label="Listed bookings",
    description=(
        "List individual bookings (booking id, room type, dates, nights, number of guests, amount, status), "
        "newest check-in first. Optional filters: status, and a check-in date range. "
        "Guest names and contact details are NOT available. Use for 'which bookings are cancelled', "
        "'show upcoming bookings'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": STATUSES},
            "start_date": DATE,
            "end_date": DATE,
            "limit": {"type": "integer", "description": "How many to return (1-20, default 10)."},
        },
    },
)
def list_bookings(hotel_id, status=None, start_date=None, end_date=None, limit=10):
    if status and status not in STATUSES:
        raise ToolInputError(f"status must be one of {', '.join(STATUSES)}.")
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
