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
