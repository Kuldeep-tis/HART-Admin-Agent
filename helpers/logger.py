"""Logging setup: one consistent format for the app and libraries."""

import logging


def init_app(app):
    level = app.config.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # The Gemini SDK and HTTP client are chatty at INFO.
    for noisy in ("httpx", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
