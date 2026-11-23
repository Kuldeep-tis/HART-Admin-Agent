"""Shared test setup.

Every test gets its own temporary databases and a FAKE Gemini client, so tests
are fast, free, repeatable and never touch the real databases or API.
"""

import importlib.util
import os
import sqlite3
import tempfile
from pathlib import Path

import pytest
from google.genai import types
from werkzeug.security import generate_password_hash

ROOT = Path(__file__).resolve().parents[1]

# Must be set BEFORE `import app`, because app.py builds a module-level app on import.
_BOOT_DIR = tempfile.mkdtemp(prefix="hart-boot-")
os.environ.update({
    "PLATFORM_DATABASE": os.path.join(_BOOT_DIR, "platform.db"),
    "HOTEL_DATABASE": os.path.join(_BOOT_DIR, "hotel.db"),
    "SECRET_KEY": "test-secret",
    "GEMINI_API_KEY": "test-key",
    "APP_ENV": "development",
    "LLM_PROVIDER": "gemini",   # tests use a fake Gemini, whatever the developer's .env says
})

from app import create_app  # noqa: E402
from apps.core.services import agent_service, user_service  # noqa: E402

PASSWORD = "correct-horse-battery"


def seed_hotel_db(path):
    spec = importlib.util.spec_from_file_location("seed_main_db", ROOT / "database" / "seed_main_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.seed(path)
    return module


# ---------- fake Gemini (real SDK response types) ----------

def text_response(text, input_tokens=100, output_tokens=10):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text=text)]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=input_tokens, candidates_token_count=output_tokens),
    )


def tool_call_response(name, args=None, call_id="call-1"):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[
            types.Part(function_call=types.FunctionCall(id=call_id, name=name, args=args or {}))
        ]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=100, candidates_token_count=5),
    )


class FakeGemini:
    """Stands in for genai.Client. Set .handler(model, contents, config) -> response (or raise)."""

    def __init__(self):
        self.calls = []
        self.handler = lambda model, contents, config: text_response("Fake answer.")

    @property
    def models(self):
        return self

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": list(contents), "config": config})
        return self.handler(model, contents, config)


def function_responses(contents):
    """All function results we sent back to Gemini in a request."""
    return [p.function_response for c in contents for p in (c.parts or []) if p.function_response]


# ---------- a "browser" that sends the CSRF token like our pages do ----------

class Browser:
    def __init__(self, client):
        self.client = client

    def csrf(self):
        with self.client.session_transaction() as s:
            s.setdefault("csrf_token", "test-csrf-token")
            return s["csrf_token"]

    def get(self, url, **kw):
        return self.client.get(url, **kw)

    def post(self, url, csrf=True, **kw):
        headers = dict(kw.pop("headers", {}) or {})
        if csrf:
            headers["X-CSRF-Token"] = self.csrf()
        return self.client.post(url, headers=headers, **kw)

    def chat(self, agent_id, message, **ids):
        return self.post(f"/dashboard/{agent_id}/preview/chat", json={"message": message, **ids})

    def login(self, email, password=PASSWORD):
        return self.post("/login", data={"email": email, "password": password})


# ---------- fixtures ----------

@pytest.fixture
def app(tmp_path):
    hotel_db = tmp_path / "hotel_info.db"
    seed_hotel_db(hotel_db)
    app = create_app({
        "TESTING": True,
        "PLATFORM_DATABASE": str(tmp_path / "platform.db"),
        "HOTEL_DATABASE": str(hotel_db),
        "LLM_PROVIDER": "gemini",
        "GEMINI_MODEL": "main-model",
        "GEMINI_FALLBACK_MODELS": ["backup-model"],
        "CHAT_MAX_PER_MINUTE": 1000,
    })
    return app


@pytest.fixture
def gemini(app):
    fake = FakeGemini()
    app.extensions["gemini_client"] = fake
    return fake


@pytest.fixture
def hotel_db(app):
    """Direct (writable) connection to the test hotel DB, to compare tool results with raw SQL."""
    conn = sqlite3.connect(app.config["HOTEL_DATABASE"])
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


@pytest.fixture
def make_user(app):
    def _make(email="admin@test.io", role="platform_admin", name="Admin"):
        with app.app_context():
            return user_service.create_user(name, email, generate_password_hash(PASSWORD), role=role)
    return _make


@pytest.fixture
def admin_id(make_user):
    return make_user()


@pytest.fixture
def agents(app, admin_id):
    """One agent per seeded hotel: {hotel_id: agent_id}. Hotel 1 Grand Palace, 2 Seaside, 3 Mountain Lodge."""
    with app.app_context():
        return {
            hid: agent_service.create_agent({"hotel_id": hid, "agent_name": f"Agent{hid}", "greeting": None}, admin_id)
            for hid in (1, 2, 3)
        }


@pytest.fixture
def browser(app):
    return Browser(app.test_client())


@pytest.fixture
def admin(app, admin_id):
    b = Browser(app.test_client())
    with b.client.session_transaction() as s:
        s["user_id"] = admin_id
    return b
