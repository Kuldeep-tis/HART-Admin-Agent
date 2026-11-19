"""Activity log: a record of who did what on the platform."""

from helpers.database import get_platform_db


def log(action, user_id=None, agent_id=None, details=None):
    db = get_platform_db()
    db.execute(
        "INSERT INTO activity_logs (user_id, agent_id, action, details) VALUES (?, ?, ?, ?)",
        (user_id, agent_id, action, details),
    )
    db.commit()


def recent(limit=10):
    return get_platform_db().execute(
        "SELECT l.*, u.name AS user_name FROM activity_logs l"
        " LEFT JOIN users u ON u.id = l.user_id"
        " ORDER BY l.created_at DESC, l.id DESC LIMIT ?",
        (limit,),
    ).fetchall()


def for_agent(agent_id, limit=20):
    return get_platform_db().execute(
        "SELECT l.*, u.name AS user_name FROM activity_logs l"
        " LEFT JOIN users u ON u.id = l.user_id"
        " WHERE l.agent_id = ?"
        " ORDER BY l.created_at DESC, l.id DESC LIMIT ?",
        (agent_id, limit),
    ).fetchall()
