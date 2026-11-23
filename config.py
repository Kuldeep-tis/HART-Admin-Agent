import os
import secrets
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
DB_DIR = BASE_DIR / "database"

# Read KEY=value lines from .env into environment variables (keeps secrets out of code).
load_dotenv(BASE_DIR / ".env")

APP_ENV = os.environ.get("APP_ENV", "development").lower()   # "development" or "production"
IS_PRODUCTION = APP_ENV == "production"


def _env_list(name, default):
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


class Config:
    APP_ENV = APP_ENV
    DEBUG = not IS_PRODUCTION and os.environ.get("FLASK_DEBUG", "1") == "1"
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

    # Signs the session cookie. Anyone who knows it can forge a login, so it must be
    # secret and random. Production refuses to start without one (see app.create_app).
    # Development falls back to a random per-process key (logins reset on restart).
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    if SECRET_KEY is None and not IS_PRODUCTION:
        SECRET_KEY = secrets.token_hex(32)
        SECRET_KEY_IS_TEMPORARY = True

    # Session cookie: not readable by JavaScript, not sent on cross-site requests,
    # HTTPS-only in production, and logins expire.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    PERMANENT_SESSION_LIFETIME = timedelta(hours=int(os.environ.get("SESSION_HOURS", "8")))

    MAX_CONTENT_LENGTH = 64 * 1024          # reject request bodies over 64 KB
    TRUST_PROXY = os.environ.get("TRUST_PROXY", "0") == "1"   # set to 1 behind nginx / a load balancer

    # Abuse limits (in memory, per server process)
    LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))     # per IP + email ...
    LOGIN_LOCKOUT_SECONDS = int(os.environ.get("LOGIN_LOCKOUT_SECONDS", "900"))  # ... per 15 minutes
    CHAT_MAX_PER_MINUTE = int(os.environ.get("CHAT_MAX_PER_MINUTE", "20"))  # per admin

    # Platform database: our own data (admins, agent configs, chats, logs). Read + write.
    PLATFORM_DATABASE = os.environ.get("PLATFORM_DATABASE", str(DB_DIR / "HART_Admin_Agent.db"))

    # Hotel database: the hotel system's data (hotels, rooms, bookings). Read-only for us.
    HOTEL_DATABASE = os.environ.get("HOTEL_DATABASE", str(DB_DIR / "hotel_info.db"))

    # Which AI answers the chats:
    #   "gemini" = Google's cloud API (data leaves our server; use only with fake data)
    #   "local"  = a model running on our own machine through Ollama (data stays with us)
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "gemini").strip().lower()

    # Local model (used when LLM_PROVIDER=local). Ollama runs as a service, like MySQL.
    OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
    OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5:3b")
    # Max seconds to wait for one model call. CPU-only machines are slow (20-60 s per answer).
    OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))
    # How long Ollama keeps the model in RAM after the last chat. Loading it again is slow,
    # so keep it longer than Ollama's default of 5 minutes. Examples: "30m", "2h", "-1" (forever).
    OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
    # How many earlier messages the local model sees. Each one must be re-read on every
    # answer (measured: ~300 history tokens = 8-11 s on CPU), and small models copy old
    # answers instead of using tools. 2 = the last question + answer, enough for follow-ups.
    OLLAMA_MAX_HISTORY = int(os.environ.get("OLLAMA_MAX_HISTORY", "2"))
    # The model's working memory in tokens. Input beyond this is silently cut from the START
    # (where our rules are), so keep prompt + tools + history well below it. More = more RAM.
    OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))

    # Gemini (used when LLM_PROVIDER=gemini)
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
    # Tried in order when a model is busy (503), out of free quota (429) or retired (404).
    GEMINI_FALLBACK_MODELS = _env_list(
        "GEMINI_FALLBACK_MODELS",
        "gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-2.5-flash,gemini-3-flash-preview",
    )
