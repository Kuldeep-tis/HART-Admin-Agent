"""Build hotel_info.db with sample hotel-system data.

Run from the project root:   python database/seed_main_db.py

This only touches hotel_info.db. The platform database (HART_Admin_Agent.db)
is never seeded: agents are configured through the platform UI.
"""

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_DB_PATH = HERE / "hotel_info.db"
SCHEMA_PATH = HERE / "hotel_info_schema.sql"

HOTELS = [
    {
        "id": 1,  # FIXED id: agents in HART_Admin_Agent.db refer to hotels by this id
        "name": "Grand Palace Hotel", "city": "Jaipur", "address": "12 MI Road, Jaipur",
        "email": "frontdesk@grandpalace.example", "phone": "+91 141 555 0101",
        "star_rating": 5, "check_in_time": "12:00", "check_out_time": "11:00",
        "policies": {
            "cancellation": "Free cancellation up to 48 hours before arrival.",
            "pets": "Pets are not allowed.",
            "smoking": "Smoking only in designated outdoor areas.",
            "payment": "Card or UPI. Full payment at check-in.",
        },
        "room_types": [("Deluxe Room", 2, 6500, 40), ("Heritage Suite", 3, 14000, 10)],
    },
    {
        "id": 2,  # FIXED id: agents in HART_Admin_Agent.db refer to hotels by this id
        "name": "Seaside Retreat", "city": "Goa", "address": "Calangute Beach Road, Goa",
        "email": "hello@seasideretreat.example", "phone": "+91 832 555 0202",
        "star_rating": 4, "check_in_time": "14:00", "check_out_time": "10:00",
        "policies": {
            "cancellation": "Non-refundable within 7 days of arrival.",
            "pets": "Pets allowed for a fee of 1,000 per night.",
            "smoking": "No smoking indoors.",
        },
        "room_types": [("Garden View", 2, 4500, 25), ("Sea View", 2, 7000, 15), ("Beach Villa", 4, 15000, 5)],
    },
    {
        "id": 3,  # FIXED id: agents in HART_Admin_Agent.db refer to hotels by this id
        "name": "Mountain Lodge", "city": "Manali", "address": "Old Manali, Himachal Pradesh",
        "email": "stay@mountainlodge.example", "phone": "+91 1902 555 0303",
        "star_rating": 3, "check_in_time": "13:00", "check_out_time": "10:30",
        "policies": {
            "cancellation": "50% refund if cancelled 72 hours before arrival.",
            "pets": "Small pets allowed.",
        },
        "room_types": [("Standard Room", 2, 2800, 20), ("Family Cottage", 5, 6000, 6)],
    },
]

GUESTS = ["Aarav Shah", "Priya Nair", "Rohan Mehta", "Ananya Iyer", "Kabir Singh",
          "Meera Joshi", "Arjun Rao", "Sara Khan", "Vikram Das", "Neha Kapoor"]


def build_bookings(rng, hotel_id, room_types, count=25):
    """Generate bookings spread from 60 days ago to 30 days ahead."""
    today = date.today()
    rows = []
    for _ in range(count):
        room_type_id, capacity, price = rng.choice(room_types)
        check_in = today + timedelta(days=rng.randint(-60, 30))
        nights = rng.randint(1, 5)
        check_out = check_in + timedelta(days=nights)
        if rng.random() < 0.15:
            status = "cancelled"
        elif check_out <= today:
            status = "checked_out"
        elif check_in <= today:
            status = "checked_in"
        else:
            status = "confirmed"
        guest = rng.choice(GUESTS)
        rows.append((
            hotel_id, room_type_id, guest, guest.lower().replace(" ", ".") + "@mail.example",
            check_in.isoformat(), check_out.isoformat(), rng.randint(1, capacity),
            price * nights, status,
        ))
    return rows


def seed(db_path=DEFAULT_DB_PATH):
    rng = random.Random(42)  # fixed seed: same data every run
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_PATH.read_text())

    for h in HOTELS:
        hotel_id = h["id"]
        conn.execute(
            "INSERT INTO hotels (id, name, city, address, email, phone, star_rating, check_in_time, check_out_time)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (hotel_id, h["name"], h["city"], h["address"], h["email"], h["phone"],
             h["star_rating"], h["check_in_time"], h["check_out_time"]),
        )

        conn.executemany(
            "INSERT INTO hotel_policies (hotel_id, policy_type, description) VALUES (?, ?, ?)",
            [(hotel_id, ptype, desc) for ptype, desc in h["policies"].items()],
        )

        room_types = []
        for name, capacity, price, total in h["room_types"]:
            rt_id = conn.execute(
                "INSERT INTO room_types (hotel_id, name, capacity, base_price, total_rooms) VALUES (?, ?, ?, ?, ?)",
                (hotel_id, name, capacity, price, total),
            ).lastrowid
            room_types.append((rt_id, capacity, price))

        conn.executemany(
            "INSERT INTO bookings (hotel_id, room_type_id, guest_name, guest_email, check_in_date,"
            " check_out_date, num_guests, total_amount, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            build_bookings(rng, hotel_id, room_types),
        )

    conn.commit()
    conn.close()
    print(f"hotel_info.db seeded with {len(HOTELS)} hotels -> {db_path}")


if __name__ == "__main__":
    seed(Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB_PATH)
