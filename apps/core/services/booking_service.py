"""Booking, availability and occupancy queries on hotel_info.db (read-only).

Every function takes hotel_id as its FIRST argument and filters by it.
Guest names, emails and phone numbers are never returned.
"""

from helpers.database import get_hotel_db

ACTIVE = "status != 'cancelled'"  # a booking that holds a room


def room_availability(hotel_id, check_in, check_out):
    """Rooms free for a stay from check_in (inclusive) to check_out (exclusive), per room type.

    Two stays overlap when: existing.check_in < new.check_out AND existing.check_out > new.check_in
    """
    rows = get_hotel_db().execute(
        f"""
        SELECT rt.name, rt.capacity, rt.base_price, rt.total_rooms,
               COUNT(b.id) AS booked
        FROM room_types rt
        LEFT JOIN bookings b
               ON b.room_type_id = rt.id AND b.hotel_id = rt.hotel_id AND b.{ACTIVE}
              AND b.check_in_date < :check_out AND b.check_out_date > :check_in
        WHERE rt.hotel_id = :hotel_id
        GROUP BY rt.id ORDER BY rt.base_price
        """,
        {"hotel_id": hotel_id, "check_in": check_in, "check_out": check_out},
    ).fetchall()
    return [
        {
            "room_type": r["name"],
            "max_guests": r["capacity"],
            "price_per_night": r["base_price"],
            "total_rooms": r["total_rooms"],
            "booked_rooms": r["booked"],
            "available_rooms": max(r["total_rooms"] - r["booked"], 0),
        }
        for r in rows
    ]


def occupancy(hotel_id, day):
    """Who is staying on `day` (the night of that date)."""
    db = get_hotel_db()
    row = db.execute(
        f"""
        SELECT
            COUNT(*) FILTER (WHERE {ACTIVE} AND check_in_date <= :day AND check_out_date > :day)                AS rooms_occupied,
            COALESCE(SUM(num_guests) FILTER (WHERE {ACTIVE} AND check_in_date <= :day AND check_out_date > :day), 0) AS guests_staying,
            COUNT(*) FILTER (WHERE status = 'checked_in')                                                         AS checked_in_bookings,
            COALESCE(SUM(num_guests) FILTER (WHERE status = 'checked_in'), 0)                                     AS checked_in_guests,
            COUNT(*) FILTER (WHERE {ACTIVE} AND check_in_date = :day)                                            AS arrivals,
            COUNT(*) FILTER (WHERE {ACTIVE} AND check_out_date = :day)                                           AS departures
        FROM bookings WHERE hotel_id = :hotel_id
        """,
        {"hotel_id": hotel_id, "day": day},
    ).fetchone()
    total_rooms = db.execute(
        "SELECT COALESCE(SUM(total_rooms), 0) FROM room_types WHERE hotel_id = ?", (hotel_id,)
    ).fetchone()[0]
    result = dict(row)
    result["date"] = day
    result["total_rooms"] = total_rooms
    result["occupancy_percent"] = round(100 * result["rooms_occupied"] / total_rooms, 1) if total_rooms else 0
    return result


def booking_summary(hotel_id, start_date, end_date):
    """Bookings whose check-in date falls between start_date and end_date (inclusive)."""
    db = get_hotel_db()
    params = {"hotel_id": hotel_id, "start": start_date, "end": end_date}
    where = "b.hotel_id = :hotel_id AND b.check_in_date BETWEEN :start AND :end"
    totals = db.execute(
        f"""
        SELECT COUNT(*) AS total_bookings,
               COUNT(*) FILTER (WHERE status = 'confirmed')   AS confirmed,
               COUNT(*) FILTER (WHERE status = 'checked_in')  AS checked_in,
               COUNT(*) FILTER (WHERE status = 'checked_out') AS checked_out,
               COUNT(*) FILTER (WHERE status = 'cancelled')   AS cancelled,
               COALESCE(SUM(total_amount) FILTER (WHERE status != 'cancelled'), 0) AS revenue,
               COALESCE(SUM(num_guests)   FILTER (WHERE status != 'cancelled'), 0) AS guests,
               ROUND(AVG(julianday(check_out_date) - julianday(check_in_date)), 1)  AS avg_nights
        FROM bookings b WHERE {where}
        """,
        params,
    ).fetchone()
    by_room = db.execute(
        f"""
        SELECT rt.name AS room_type, COUNT(*) AS bookings,
               COALESCE(SUM(b.total_amount) FILTER (WHERE b.status != 'cancelled'), 0) AS revenue
        FROM bookings b JOIN room_types rt ON rt.id = b.room_type_id AND rt.hotel_id = b.hotel_id
        WHERE {where} GROUP BY rt.id ORDER BY bookings DESC
        """,
        params,
    ).fetchall()
    result = {"start_date": start_date, "end_date": end_date, **dict(totals)}
    result["cancellation_rate_percent"] = (
        round(100 * result["cancelled"] / result["total_bookings"], 1) if result["total_bookings"] else 0
    )
    result["by_room_type"] = [dict(r) for r in by_room]
    return result


def list_bookings(hotel_id, status=None, start_date=None, end_date=None, limit=10, booking_id=None):
    """Individual bookings WITHOUT personal data (no guest name, email or phone)."""
    sql = (
        "SELECT b.id AS booking_id, rt.name AS room_type, b.check_in_date, b.check_out_date,"
        " CAST(julianday(b.check_out_date) - julianday(b.check_in_date) AS INTEGER) AS nights,"
        " b.num_guests, b.total_amount, b.status"
        " FROM bookings b JOIN room_types rt ON rt.id = b.room_type_id AND rt.hotel_id = b.hotel_id"
        " WHERE b.hotel_id = ?"
    )
    params = [hotel_id]
    if booking_id is not None:   # still inside this hotel: another hotel's booking is "not found"
        sql += " AND b.id = ?"
        params.append(booking_id)
    if status:
        sql += " AND b.status = ?"
        params.append(status)
    if start_date:
        sql += " AND b.check_in_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND b.check_in_date <= ?"
        params.append(end_date)
    count = get_hotel_db().execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    rows = get_hotel_db().execute(sql + " ORDER BY b.check_in_date DESC LIMIT ?", [*params, limit]).fetchall()
    return {"matching_bookings": count, "showing": len(rows), "bookings": [dict(r) for r in rows]}
