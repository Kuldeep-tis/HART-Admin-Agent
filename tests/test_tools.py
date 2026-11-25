"""Tool results match the database, and bad arguments are handled."""

from datetime import date, timedelta

import pytest

from apps.core import tools
from apps.core.tools.registry import period_in, resolve_period, steer_period

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


def test_unknown_arguments_are_an_error_not_silently_dropped(app):
    # Dropping them silently made the AI believe it had filtered (e.g. "id": 3 -> newest bookings).
    result = run(app, "list_bookings", id="3")
    assert "has no argument id" in result["error"] and "booking_id" in result["error"]
    assert "has no argument sql" in run(app, "get_policies", sql="DROP TABLE bookings")["error"]


def test_hotel_id_from_the_ai_is_silently_ignored(app):
    result = run(app, "get_policies", hotel_id=1)   # security: we always use our own hotel_id
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


@pytest.mark.parametrize("name", ["get_booking_summary", "list_bookings"])
def test_period_and_dates_together_is_an_error(app, name):
    # "this_month" + end date 30 Dec: silently picking one gave wrong answers, so the AI must choose.
    result = run(app, name, period="this_month", end_date=(TODAY + timedelta(days=36)).isoformat())
    assert "either period or start_date/end_date" in result["error"] and "next_30_days" in result["error"]   # tells the AI how to retry


def test_upcoming_bookings_for_the_next_30_days(app, hotel_db):
    result = run(app, "list_bookings", status="confirmed", period="next_30_days", limit=20)
    end = (TODAY + timedelta(days=29)).isoformat()
    expected = {r[0] for r in hotel_db.execute(
        "SELECT id FROM bookings WHERE hotel_id = 2 AND status = 'confirmed'"
        " AND check_in_date BETWEEN ? AND ?", (TODAY.isoformat(), end))}
    assert {b["booking_id"] for b in result["bookings"]} == expected
    assert all(TODAY.isoformat() <= b["check_in_date"] <= end for b in result["bookings"])


# ---------- one booking by its number ----------

def test_one_booking_by_id(app, hotel_db):
    own = hotel_db.execute("SELECT id FROM bookings WHERE hotel_id = 2 ORDER BY id LIMIT 1").fetchone()[0]
    for value in (own, str(own), f"#{own}"):
        result = run(app, "list_bookings", booking_id=value, status="confirmed")  # other filters don't hide it
        assert [b["booking_id"] for b in result["bookings"]] == [own]
    assert f"#{own} ·" in tools.render("list_bookings", result)


def test_another_hotels_booking_is_not_found(app, hotel_db):
    other = hotel_db.execute("SELECT id FROM bookings WHERE hotel_id = 1 LIMIT 1").fetchone()[0]
    result = run(app, "list_bookings", booking_id=other)
    assert result["bookings"] == []
    assert tools.render("list_bookings", result) == f"Booking #{other} was not found."


def test_bad_booking_id_is_an_error(app):
    assert "booking_id must be a booking number" in run(app, "list_bookings", booking_id="abc")["error"]


# ---------- availability for one room type ----------

def test_availability_for_one_room_type(app):
    day = TODAY.isoformat()
    for asked in ("sea view", "Sea View rooms", "sea-view room"):
        result = run(app, "check_room_availability", check_in=day, room_type=asked)
        assert [r["room_type"] for r in result["room_types"]] == ["Sea View"]
        assert result["total_available_rooms"] == result["room_types"][0]["available_rooms"]
    error = run(app, "check_room_availability", check_in=day, room_type="Heritage Suite")["error"]
    assert "No room type" in error and "Sea View" in error   # another hotel's room type is unknown here


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


# ---------- the period the staff member named ----------

@pytest.mark.parametrize("question, period", [
    ("and last month?", "last_month"),
    ("What were LAST MONTH's sales", "last_month"),
    ("revenue this month so far", "this_month"),
    ("upcoming confirmed bookings for the next 30 days", "next_30_days"),
    ("bookings in the last 30 days", "last_30_days"),    # not also "30 days" or "last month"
    ("any arrivals today?", "today"),
    ("compare this month and last month", None),        # two periods: the AI decides
    ("how many rooms are free?", None),
])
def test_period_in_question(question, period):
    assert period_in(question) == period


def test_steer_period_only_touches_tools_with_a_period():
    wrong = {"period": "this_month", "end_date": "2026-11-24", "status": "cancelled"}
    assert steer_period("list_bookings", wrong, "and last month?") == {"period": "last_month", "status": "cancelled"}
    assert steer_period("list_bookings", wrong, "show cancelled ones") == wrong        # no period named
    availability = {"check_in": "2026-11-24"}
    assert steer_period("check_room_availability", availability, "rooms free today?") == availability
