# HART Admin Agent

A platform for configuring one AI agent per hotel. Each agent answers questions
by calling read-only database tools for **its own hotel only**, using Gemini.

## How it fits together

```
Browser ─► Flask (apps/admin/routes.py)
             ├─ HART_Admin_Agent.db   our data: admins, agents, conversations, messages, activity log
             └─ apps/core/agent.py ─► Gemini (model queue with fallbacks)
                    └─ apps/core/tools/ ─► hotel_info.db  (opened READ-ONLY, always filtered by the agent's hotel_id)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

cp .env.example .env               # then fill in GEMINI_API_KEY and SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"   # paste as SECRET_KEY

python database/seed_main_db.py    # sample hotel data (hotel_info.db)
flask --app app create-admin       # your platform admin login
```

`HART_Admin_Agent.db` is created and upgraded automatically on start (new tables and
columns are added; existing data is never deleted).

## Run

**Development** (auto-reload, http):

```bash
python app.py                      # http://127.0.0.1:5000
```

**Production** (behind HTTPS):

```bash
APP_ENV=production gunicorn -w 2 -b 127.0.0.1:8000 app:app
```

In production:
- `SECRET_KEY` is required (the app refuses to start without it).
- Login cookies are `Secure`, so the site **must be served over HTTPS** (e.g. nginx in front).
  Set `TRUST_PROXY=1` when behind nginx / a load balancer.
- The debugger is off; errors show a friendly page and are logged.
- Rate limits are kept in memory per worker process. For several servers, move them to Redis.

## Tests

```bash
pytest
```

Uses temporary databases and a fake Gemini, so it is fast, free and never touches
real data. Covers:

| File | What it proves |
|---|---|
| `tests/test_isolation.py` | Every tool returns only its own hotel's data; the AI can't choose `hotel_id`; sessions, history and transcripts can't cross agents or guests; no guest personal data; hotel DB is read-only |
| `tests/test_security.py` | Login required, role check, CSRF on every form and the chat API, login lockout, session fixation, cookie flags, security headers, HTML escaping, size and rate limits |
| `tests/test_chat.py` | Validation, conversation storage, history from the DB, tool loop, error handling, model fallback queue |
| `tests/test_tools.py` | Tool results match raw SQL; bad arguments return errors instead of crashing |
| `tests/test_database.py` | Restarts keep data, old databases are migrated, seeded hotel ids are fixed |

## Security model

- **Tenant isolation:** an agent's `hotel_id` comes from the `agents` table. Tools receive it from
  our code, never from the AI or the browser, and every query filters by it (joins check it too).
- **Hotel data is read-only:** `hotel_info.db` is opened with `mode=ro`.
- **Chats:** the server reads history from the database; a session only continues when both the
  agent and the guest id match.
- **Web:** CSRF tokens, Content-Security-Policy (only our own scripts run), HttpOnly/SameSite
  cookies, login lockout, timing-safe login, request size limit.
- **Roles:** only `platform_admin` exists and can see all hotels. Any other role is refused (403)
  until per-hotel access is built.

## Known limits / next steps

- Hotel admins (users who see only their own hotel) are not built yet.
- The booking tools expose counts and revenue. Fine for admins, but a public guest chat should get
  a smaller tool set (hotel info, policies, rooms, availability).
- On Gemini's free tier, Google may use prompts to improve its products. Don't send real guest data there.
