"""Chat storage: guests, conversations (sessions) and messages. Platform DB."""

import json
import uuid

from helpers.database import get_platform_db


def _valid_uuid(value):
    try:
        return str(uuid.UUID(str(value))) == str(value).lower()
    except (ValueError, TypeError):
        return False


# ---------- Guests ----------

def get_or_create_guest(public_id=None):
    """Return the guest for this browser id, creating one if it's new or invalid."""
    db = get_platform_db()
    if public_id and _valid_uuid(public_id):
        guest = db.execute("SELECT * FROM guests WHERE public_id = ?", (public_id.lower(),)).fetchone()
        if guest:
            db.execute("UPDATE guests SET last_seen_at = CURRENT_TIMESTAMP WHERE id = ?", (guest["id"],))
            db.commit()
            return guest
    else:
        public_id = None
    public_id = (public_id or str(uuid.uuid4())).lower()
    cur = db.execute("INSERT INTO guests (public_id) VALUES (?)", (public_id,))
    db.commit()
    return db.execute("SELECT * FROM guests WHERE id = ?", (cur.lastrowid,)).fetchone()


# ---------- Conversations ----------

def find_conversation(session_id, agent_id, guest_id):
    """A conversation only matches if it belongs to THIS agent and THIS guest."""
    if not session_id or not _valid_uuid(session_id):
        return None
    return get_platform_db().execute(
        "SELECT * FROM conversations WHERE session_id = ? AND agent_id = ? AND guest_id = ?",
        (str(session_id).lower(), agent_id, guest_id),
    ).fetchone()


def start_conversation(agent, guest_id, channel, started_by, greeting):
    """Create a new session and store the agent's greeting as its first message."""
    db = get_platform_db()
    cur = db.execute(
        "INSERT INTO conversations (session_id, agent_id, hotel_id, guest_id, channel, started_by)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), agent["id"], agent["hotel_id"], guest_id, channel, started_by),
    )
    db.commit()
    add_message(cur.lastrowid, "assistant", greeting)
    return get_conversation(cur.lastrowid)


def get_conversation(conversation_id):
    return get_platform_db().execute(
        "SELECT c.*, g.public_id AS guest_public_id, u.name AS started_by_name"
        " FROM conversations c"
        " JOIN guests g ON g.id = c.guest_id"
        " LEFT JOIN users u ON u.id = c.started_by"
        " WHERE c.id = ?",
        (conversation_id,),
    ).fetchone()


def list_for_agent(agent_id, limit=100):
    return get_platform_db().execute(
        "SELECT c.*, g.public_id AS guest_public_id,"
        " (SELECT content FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'"
        "  ORDER BY m.id LIMIT 1) AS first_question"
        " FROM conversations c JOIN guests g ON g.id = c.guest_id"
        " WHERE c.agent_id = ?"
        " ORDER BY c.last_message_at DESC, c.id DESC LIMIT ?",
        (agent_id, limit),
    ).fetchall()


def stats_for_agent(agent_id):
    row = get_platform_db().execute(
        "SELECT COUNT(DISTINCT c.id) AS conversations, COUNT(DISTINCT c.guest_id) AS guests,"
        " COUNT(m.id) FILTER (WHERE m.role = 'user') AS questions,"
        " CAST(AVG(m.latency_ms) FILTER (WHERE m.role = 'assistant' AND m.status = 'ok') AS INTEGER) AS avg_latency_ms,"
        " COALESCE(SUM(m.input_tokens), 0) + COALESCE(SUM(m.output_tokens), 0) AS tokens"
        " FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id"
        " WHERE c.agent_id = ?",
        (agent_id,),
    ).fetchone()
    return dict(row)


# ---------- Messages ----------

def add_message(conversation_id, role, content, status="ok", model=None,
                input_tokens=None, output_tokens=None, latency_ms=None, tool_calls=None,
                first_token_ms=None):
    db = get_platform_db()
    db.execute(
        "INSERT INTO messages (conversation_id, role, content, status, model, input_tokens, output_tokens,"
        " latency_ms, tool_calls, first_token_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conversation_id, role, content, status, model, input_tokens, output_tokens, latency_ms,
         json.dumps(tool_calls, default=str) if tool_calls else None, first_token_ms),
    )
    db.execute(
        "UPDATE conversations SET message_count = message_count + 1, last_message_at = CURRENT_TIMESTAMP"
        " WHERE id = ?",
        (conversation_id,),
    )
    db.commit()


def get_messages(conversation_id):
    return get_platform_db().execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,)
    ).fetchall()


def get_history(conversation_id, max_turns=20):
    """Last successful turns, oldest first, in the format respond() expects.

    Failed AI calls (status='error') are left out so the AI never sees error text.
    """
    rows = get_platform_db().execute(
        "SELECT role, content FROM messages WHERE conversation_id = ? AND status = 'ok'"
        " ORDER BY id DESC LIMIT ?",
        (conversation_id, max_turns),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
