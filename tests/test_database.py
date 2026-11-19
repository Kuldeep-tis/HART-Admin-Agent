"""Schema creation, migrations and the seed script."""

import sqlite3

from app import create_app
from tests.conftest import seed_hotel_db


def test_restart_keeps_platform_data(app, agents, tmp_path):
    again = create_app({"PLATFORM_DATABASE": app.config["PLATFORM_DATABASE"],
                        "HOTEL_DATABASE": app.config["HOTEL_DATABASE"]})
    conn = sqlite3.connect(again.config["PLATFORM_DATABASE"])
    assert conn.execute("SELECT COUNT(*) FROM agents").fetchone()[0] == 3


def test_old_database_gets_new_columns_without_losing_data(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE agents (id INTEGER PRIMARY KEY, hotel_id INTEGER NOT NULL UNIQUE, agent_name TEXT NOT NULL,
            greeting TEXT, status TEXT NOT NULL DEFAULT 'active', created_by INTEGER,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO agents (hotel_id, agent_name) VALUES (2, 'Kia');
    """)
    conn.commit(); conn.close()

    create_app({"PLATFORM_DATABASE": str(db), "HOTEL_DATABASE": str(tmp_path / "h.db")})

    conn = sqlite3.connect(db)
    agent_cols = {r[1] for r in conn.execute("PRAGMA table_info(agents)")}
    message_cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
    assert "admin_prompt" in agent_cols and "tool_calls" in message_cols
    assert conn.execute("SELECT agent_name FROM agents").fetchone()[0] == "Kia"


def test_seed_gives_hotels_fixed_ids(tmp_path):
    db = tmp_path / "hotel.db"
    for _ in range(2):  # re-seeding must not change ids
        module = seed_hotel_db(db)
    ids = dict(sqlite3.connect(db).execute("SELECT name, id FROM hotels").fetchall())
    assert ids == {h["name"]: h["id"] for h in module.HOTELS}
