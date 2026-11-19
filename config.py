import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"


class Config:
    # Signs the session cookie. Override with a long random value outside development.
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")

    # Platform database: our own data (admins, agent configs, logs). Read + write.
    PLATFORM_DATABASE = os.environ.get("PLATFORM_DATABASE", str(DB_DIR / "HART_Admin_Agent.db"))

    # Hotel database: the hotel system's data (hotels, rooms, bookings). Read-only for us.
    HOTEL_DATABASE = os.environ.get("HOTEL_DATABASE", str(DB_DIR / "hotel_info.db"))
