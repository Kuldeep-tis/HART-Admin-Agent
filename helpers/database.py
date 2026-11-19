"""Connections to our two databases.

- Platform DB (HART_Admin_Agent.db): our data. Read + write.
- Hotel DB (hotel_info.db): the hotel system's data. Opened READ-ONLY,
  so nothing in the platform (or later, the AI agent) can change it.
"""

import sqlite3
from pathlib import Path

from flask import current_app, g

PLATFORM_SCHEMA = Path(__file__).resolve().parent.parent / "database" / "platform_schema.sql"


class HotelDatabaseMissing(RuntimeError):
    """hotel_info.db has not been created yet."""


def _connect(path, read_only=False):
    if read_only:
        # URI form lets us pass mode=ro: any INSERT/UPDATE/DELETE raises an error.
        conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row  # rows behave like dicts: row["name"]
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_platform_db():
    """One platform connection per request, reused within that request."""
    if "platform_db" not in g:
        g.platform_db = _connect(current_app.config["PLATFORM_DATABASE"])
    return g.platform_db


def get_hotel_db():
    """One read-only hotel connection per request."""
    if "hotel_db" not in g:
        path = current_app.config["HOTEL_DATABASE"]
        if not Path(path).exists():
            raise HotelDatabaseMissing(path)
        g.hotel_db = _connect(path, read_only=True)
    return g.hotel_db


def close_dbs(exc=None):
    for key in ("platform_db", "hotel_db"):
        conn = g.pop(key, None)
        if conn is not None:
            conn.close()


# Columns added after the first release. "CREATE TABLE IF NOT EXISTS" never changes
# an existing table, so existing databases get these via ALTER TABLE instead.
#   (table, column, column definition)
ADDED_COLUMNS = [
    ("agents", "admin_prompt", "TEXT"),
    ("messages", "tool_calls", "TEXT"),
]


def _add_missing_columns(conn):
    for table, column, definition in ADDED_COLUMNS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    conn.commit()


def ensure_platform_schema(app):
    """Create platform tables if they don't exist, add new columns. Never deletes data."""
    path = Path(app.config["PLATFORM_DATABASE"])
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(path)
    conn.executescript(PLATFORM_SCHEMA.read_text())
    _add_missing_columns(conn)
    conn.close()


def init_app(app):
    ensure_platform_schema(app)            # runs once at startup
    app.teardown_appcontext(close_dbs)     # runs after every request
