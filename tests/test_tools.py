"""Tool results match the database, and bad arguments are handled."""

from datetime import date, timedelta

import pytest

from apps.core import tools

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
