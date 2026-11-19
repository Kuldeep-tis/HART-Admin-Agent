-- HART_Admin_Agent.db: the platform's own data.
-- Runs on every app start. "IF NOT EXISTS" makes it safe: existing data is never touched.

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'platform_admin',
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One configured AI agent per hotel.
-- hotel_id points at hotels.id in hotel_info.db. It is a different database file,
-- so SQLite cannot enforce it as a FOREIGN KEY; the app checks it instead.
CREATE TABLE IF NOT EXISTS agents (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    hotel_id    INTEGER NOT NULL UNIQUE,
    agent_name  TEXT NOT NULL,
    greeting    TEXT,
    admin_prompt TEXT,               -- extra instructions added to the AI's system prompt
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'inactive')),
    created_by  INTEGER REFERENCES users (id),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Who did what, and when.
CREATE TABLE IF NOT EXISTS activity_logs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER REFERENCES users (id),
    agent_id   INTEGER REFERENCES agents (id) ON DELETE SET NULL,
    action     TEXT NOT NULL,
    details    TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_activity_logs_created_at ON activity_logs (created_at);

-- ---------- Chat storage ----------

-- A website visitor. Anonymous: identified only by a random id kept in their browser.
-- No name, email or IP address is stored.
CREATE TABLE IF NOT EXISTS guests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id     TEXT NOT NULL UNIQUE,          -- UUID stored in the browser
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- One chat session between a guest and one hotel's agent.
CREATE TABLE IF NOT EXISTS conversations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL UNIQUE,        -- UUID the browser sends with every message
    agent_id        INTEGER NOT NULL REFERENCES agents (id) ON DELETE CASCADE,
    hotel_id        INTEGER NOT NULL,            -- copied from the agent: simple, safe per-hotel queries
    guest_id        INTEGER NOT NULL REFERENCES guests (id),
    channel         TEXT NOT NULL DEFAULT 'preview' CHECK (channel IN ('preview', 'website')),
    started_by      INTEGER REFERENCES users (id),   -- admin who ran a preview; NULL for real guests
    message_count   INTEGER NOT NULL DEFAULT 0,
    started_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_message_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_conversations_agent ON conversations (agent_id, last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversations_hotel ON conversations (hotel_id, last_message_at);
CREATE INDEX IF NOT EXISTS idx_conversations_guest ON conversations (guest_id);

-- Every message in a conversation, plus how the AI answer was produced.
CREATE TABLE IF NOT EXISTS messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role            TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok', 'error')),  -- error = AI call failed
    model           TEXT,                        -- e.g. gemini-3.5-flash-lite (assistant only)
    input_tokens    INTEGER,
    output_tokens   INTEGER,
    latency_ms      INTEGER,                     -- how long the AI took to answer
    tool_calls      TEXT,                        -- JSON list of database tools the AI used
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, id);
