"""Agent configurations, stored in HART_Admin_Agent.db (platform DB)."""

from apps.core.services import hotel_service
from helpers.database import get_platform_db


def list_agents_with_hotels():
    """Each agent plus its hotel's details.

    The two tables live in different database files, so we can't SQL JOIN them.
    Instead: fetch agents, fetch their hotels in one query, combine in Python.
    """
    agents = get_platform_db().execute(
        "SELECT * FROM agents ORDER BY created_at DESC, id DESC"
    ).fetchall()
    hotels = hotel_service.get_hotels_by_ids(a["hotel_id"] for a in agents)
    return [{"agent": a, "hotel": hotels.get(a["hotel_id"])} for a in agents]


def get_agent(agent_id):
    return get_platform_db().execute("SELECT * FROM agents WHERE id = ?", (agent_id,)).fetchone()


def hotel_has_agent(hotel_id):
    row = get_platform_db().execute(
        "SELECT 1 FROM agents WHERE hotel_id = ?", (hotel_id,)
    ).fetchone()
    return row is not None


def unconfigured_hotels():
    """Hotels from hotel_info.db that don't have an agent yet."""
    configured = {
        row["hotel_id"] for row in get_platform_db().execute("SELECT hotel_id FROM agents")
    }
    return [h for h in hotel_service.list_hotels() if h["id"] not in configured]


def create_agent(data, created_by):
    db = get_platform_db()
    cur = db.execute(
        "INSERT INTO agents (hotel_id, agent_name, greeting, created_by) VALUES (?, ?, ?, ?)",
        (data["hotel_id"], data["agent_name"], data["greeting"], created_by),
    )
    db.commit()
    return cur.lastrowid


def update_agent(agent_id, data):
    db = get_platform_db()
    db.execute(
        "UPDATE agents SET agent_name = ?, greeting = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (data["agent_name"], data["greeting"], agent_id),
    )
    db.commit()


def toggle_status(agent_id):
    db = get_platform_db()
    db.execute(
        "UPDATE agents SET status = CASE status WHEN 'active' THEN 'inactive' ELSE 'active' END,"
        " updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (agent_id,),
    )
    db.commit()


def status_counts():
    row = get_platform_db().execute(
        "SELECT COUNT(*) AS total,"
        " SUM(status = 'active') AS active,"
        " SUM(status = 'inactive') AS inactive FROM agents"
    ).fetchone()
    return {"total": row["total"], "active": row["active"] or 0, "inactive": row["inactive"] or 0}
