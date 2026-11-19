-- hotel_info.db: simulates the hotel system's own database.
-- Rebuilt from scratch every time seed_main_db.py runs.

DROP TABLE IF EXISTS bookings;
DROP TABLE IF EXISTS room_types;
DROP TABLE IF EXISTS hotel_policies;
DROP TABLE IF EXISTS hotels;

CREATE TABLE hotels (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    city           TEXT NOT NULL,
    address        TEXT,
    email          TEXT,
    phone          TEXT,
    star_rating    INTEGER CHECK (star_rating BETWEEN 1 AND 5),
    check_in_time  TEXT NOT NULL,
    check_out_time TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE hotel_policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id    INTEGER NOT NULL REFERENCES hotels (id) ON DELETE CASCADE,
    policy_type TEXT NOT NULL,          -- cancellation, pets, smoking, payment, ...
    description TEXT NOT NULL
);

CREATE TABLE room_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id    INTEGER NOT NULL REFERENCES hotels (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    capacity    INTEGER NOT NULL,
    base_price  REAL NOT NULL,          -- per night
    total_rooms INTEGER NOT NULL
);

CREATE TABLE bookings (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id       INTEGER NOT NULL REFERENCES hotels (id) ON DELETE CASCADE,
    room_type_id   INTEGER NOT NULL REFERENCES room_types (id),
    guest_name     TEXT NOT NULL,
    guest_email    TEXT,
    check_in_date  TEXT NOT NULL,       -- YYYY-MM-DD
    check_out_date TEXT NOT NULL,
    num_guests     INTEGER NOT NULL,
    total_amount   REAL NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled', 'checked_in', 'checked_out')),
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_bookings_hotel_dates ON bookings (hotel_id, check_in_date);
