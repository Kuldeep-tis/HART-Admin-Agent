"""Tool results match the database, and bad arguments are handled."""

from datetime import date, timedelta

import pytest

from apps.core import tools
from apps.core.tools.registry import resolve_period

TODAY = date.today()


def run(app, name, hotel_id=2, **args):
    with app.test_request_context():
        return tools.execute(name, args, hotel_id=hotel_id)


def test_availability_matches_sql(app, hotel_db):
    d1, d2 = TODAY.isoformat(), (TODAY + timedelta(days=2)).isoformat()
    result = run(app, "check_room_availability", check_in=d1, check_out=d2)
    for rt in result["room_types"]:
        booked = hotel_db.execute(
            "SELECT COUNT(*) FROM bookings b JOIN room_types r ON r.id = b.room_type_id"
            " WHERE b.hotel_id = 2 AND r.name = ? AND b.status != 'cancelled'"
            " AND b.check_in_date < ? AND b.check_out_date > ?", (rt["room_type"], d2, d1)).fetchone()[0]
        assert rt["booked_rooms"] == booked
        assert rt["available_rooms"] == rt["total_rooms"] - booked


def test_checked_in_guests_match_sql(app, hotel_db):
    result = run(app, "get_occupancy")
    count, guests = hotel_db.execute(
        "SELECT COUNT(*), COALESCE(SUM(num_guests), 0) FROM bookings WHERE hotel_id = 2 AND status = 'checked_in'").fetchone()
    assert (result["checked_in_bookings"], result["checked_in_guests"]) == (count, guests)


def test_booking_summary_matches_sql(app, hotel_db):
    start, end = TODAY.replace(day=1).isoformat(), TODAY.isoformat()
    result = run(app, "get_booking_summary", start_date=start, end_date=end)
    total, revenue = hotel_db.execute(
        "SELECT COUNT(*), COALESCE(SUM(CASE WHEN status != 'cancelled' THEN total_amount END), 0)"
        " FROM bookings WHERE hotel_id = 2 AND check_in_date BETWEEN ? AND ?", (start, end)).fetchone()
    assert (result["total_bookings"], result["revenue"]) == (total, revenue)


def test_list_bookings_filters_and_limits(app):
    result = run(app, "list_bookings", status="cancelled", limit=500)
    assert all(b["status"] == "cancelled" for b in result["bookings"])
    assert result["showing"] <= 20  # limit is capped


@pytest.mark.parametrize("name, args, message", [
    ("check_room_availability", {"check_in": "next friday"}, "YYYY-MM-DD"),
    ("check_room_availability", {}, "check_in is required"),
    ("check_room_availability", {"check_in": "2030-01-05", "check_out": "2030-01-01"}, "must be after"),
    ("get_booking_summary", {"start_date": "2020-01-01", "end_date": "2026-12-31"}, "at most"),
    ("list_bookings", {"status": "deleted"}, "status must be one of"),
    ("drop_tables", {}, "Unknown tool"),
])
def test_bad_arguments_return_an_error_instead_of_crashing(app, name, args, message):
    assert message in run(app, name, **args)["error"]


def test_unexpected_arguments_are_dropped(app):
    result = run(app, "get_policies", sql="DROP TABLE bookings", hotel_id=1)
    assert "policies" in result


# ---------- named periods: Python does the date maths ----------

@pytest.mark.parametrize("period, today, expected", [
    ("last_month", date(2026, 1, 15), (date(2025, 12, 1), date(2025, 12, 31))),   # across a year
    ("last_month", date(2026, 3, 31), (date(2026, 2, 1), date(2026, 2, 28))),
    ("this_month", date(2026, 11, 23), (date(2026, 11, 1), date(2026, 11, 23))),  # "so far": ends today
    ("this_week", date(2026, 11, 25), (date(2026, 11, 23), date(2026, 11, 25))),  # Monday..today
    ("last_week", date(2026, 11, 25), (date(2026, 11, 16), date(2026, 11, 22))),
    ("yesterday", date(2026, 3, 1), (date(2026, 2, 28), date(2026, 2, 28))),
    ("next_7_days", date(2026, 11, 23), (date(2026, 11, 23), date(2026, 11, 29))),
])
def test_periods_resolve_to_the_right_dates(period, today, expected):
    assert resolve_period(period, today) == expected


def test_period_gives_the_same_summary_as_explicit_dates(app):
    start, end = resolve_period("last_month")
    by_period = run(app, "get_booking_summary", period="last_month")
    by_dates = run(app, "get_booking_summary", start_date=start.isoformat(), end_date=end.isoformat())
    assert by_period == by_dates and by_period["start_date"] == start.isoformat()


def test_period_wins_over_wrong_dates_from_the_model(app):
    result = run(app, "get_booking_summary", period="this_month", start_date="2026-01-01", end_date="2026-01-31")
    assert result["start_date"] == TODAY.replace(day=1).isoformat()


def test_summary_without_dates_means_this_month_so_far(app):
    result = run(app, "get_booking_summary")
    assert (result["start_date"], result["end_date"]) == (TODAY.replace(day=1).isoformat(), TODAY.isoformat())


def test_unknown_period_is_an_error(app):
    assert "period must be one of" in run(app, "list_bookings", period="last_decade")["error"]


# ---------- Python-written answers ----------

def test_booking_list_is_rendered_by_python(app):
    result = run(app, "list_bookings", status="cancelled")
    text = tools.render("list_bookings", result)
    assert text.startswith(f"{result['matching_bookings']} booking")
    for b in result["bookings"]:
        assert f"#{b['booking_id']} · {b['room_type']}" in text
    assert "cancelled" in text and "₹" in text


def test_empty_list_and_errors_are_not_rendered(app):
    empty = run(app, "list_bookings", start_date="2030-01-01", end_date="2030-01-02")
    assert tools.render("list_bookings", empty) == "No bookings match that."
    assert tools.render("list_bookings", {"error": "bad status"}) is None   # the AI explains errors
    assert tools.render("get_room_types", run(app, "get_room_types")) is None  # no renderer


def test_booking_table_matches_the_text_answer(app):
    result = run(app, "list_bookings", status="cancelled")
    table = tools.table("list_bookings", result)
    assert table["columns"] == ["Booking", "Stay", "Guests", "Amount", "Status"]
    assert [row[0].split("\n")[1] for row in table["rows"]] == [f"#{b['booking_id']}" for b in result["bookings"]]
    assert all(row[4] == "cancelled" for row in table["rows"])
    assert tools.render("list_bookings", result).startswith(table["title"])   # same title in both
    empty = run(app, "list_bookings", start_date="2030-01-01", end_date="2030-01-02")
    assert tools.table("list_bookings", empty) is None   # nothing to draw: the text answer is used


@pytest.mark.parametrize("check_in, check_out, expected", [
    ("2026-11-18", "2026-11-20", "18 → 20 Nov"),        # same month: month once
    ("2026-10-30", "2026-11-02", "30 Oct → 2 Nov"),     # across months
    ("2027-01-03", "2027-01-05", "3 → 5 Jan 2027"),     # another year: show it
])
def test_stay_dates_are_short(check_in, check_out, expected, monkeypatch):
    from apps.core.tools import booking_tools
    monkeypatch.setattr(booking_tools, "_today", lambda: date(2026, 11, 23))
    assert booking_tools._stay(date.fromisoformat(check_in), date.fromisoformat(check_out)) == expected
