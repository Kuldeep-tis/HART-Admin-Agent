"""Read-only access to hotel_info.db (the hotel system's data)."""

from datetime import date

from helpers.database import get_hotel_db


def list_hotels():
    return get_hotel_db().execute("SELECT * FROM hotels ORDER BY name").fetchall()


def get_hotel(hotel_id):
    return get_hotel_db().execute("SELECT * FROM hotels WHERE id = ?", (hotel_id,)).fetchone()


def get_hotels_by_ids(hotel_ids):
    """Return {hotel_id: row} for many hotels in one query."""
    ids = list(hotel_ids)
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = get_hotel_db().execute(
        f"SELECT * FROM hotels WHERE id IN ({placeholders})", ids
    ).fetchall()
    return {row["id"]: row for row in rows}


def get_policies(hotel_id):
    return get_hotel_db().execute(
        "SELECT policy_type, description FROM hotel_policies WHERE hotel_id = ? ORDER BY policy_type",
        (hotel_id,),
    ).fetchall()


def get_room_types(hotel_id):
    return get_hotel_db().execute(
        "SELECT * FROM room_types WHERE hotel_id = ? ORDER BY base_price", (hotel_id,)
    ).fetchall()


def get_room_types_with_bookings(hotel_id):
    """Room types plus how many active (non-cancelled) bookings each has."""
    return get_hotel_db().execute(
        "SELECT rt.*, COUNT(b.id) AS booking_count"
        " FROM room_types rt"
        " LEFT JOIN bookings b ON b.room_type_id = rt.id AND b.status != 'cancelled'"
        " WHERE rt.hotel_id = ?"
        " GROUP BY rt.id ORDER BY rt.base_price",
        (hotel_id,),
    ).fetchall()


def get_booking_stats(hotel_id):
    """Headline numbers for one hotel, calculated in SQL."""
    today = date.today().isoformat()
    row = get_hotel_db().execute(
        """
        SELECT
            COUNT(*)                                                         AS total,
            SUM(status = 'cancelled')                                        AS cancelled,
            SUM(status = 'confirmed' AND check_in_date > :today)             AS upcoming,
            SUM(status != 'cancelled'
                AND check_in_date <= :today AND check_out_date > :today)     AS in_house,
            COALESCE(SUM(CASE WHEN status != 'cancelled' THEN total_amount END), 0) AS revenue
        FROM bookings WHERE hotel_id = :hotel_id
        """,
        {"today": today, "hotel_id": hotel_id},
    ).fetchone()
    total_rooms = get_hotel_db().execute(
        "SELECT COALESCE(SUM(total_rooms), 0) FROM room_types WHERE hotel_id = ?", (hotel_id,)
    ).fetchone()[0]

    stats = {k: row[k] or 0 for k in row.keys()}
    stats["total_rooms"] = total_rooms
    stats["occupancy_pct"] = round(100 * stats["in_house"] / total_rooms) if total_rooms else 0
    stats["cancel_pct"] = round(100 * stats["cancelled"] / stats["total"]) if stats["total"] else 0
    return stats


def get_recent_bookings(hotel_id, limit=15):
    return get_hotel_db().execute(
        "SELECT b.*, rt.name AS room_type FROM bookings b"
        " JOIN room_types rt ON rt.id = b.room_type_id"
        " WHERE b.hotel_id = ?"
        " ORDER BY b.check_in_date DESC, b.id DESC LIMIT ?",
        (hotel_id, limit),
    ).fetchall()
