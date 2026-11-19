"""Web security: CSRF protection, security headers and simple rate limiting.

CSRF (cross-site request forgery): a malicious site could make a logged-in
admin's browser submit our forms. Every POST must therefore carry a secret
token that only our own pages know (stored in the signed session cookie).
"""

import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from flask import abort, current_app, jsonify, request, session

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def csrf_token():
    """The token for this session (created on first use). Used in templates."""
    if CSRF_SESSION_KEY not in session:
        session[CSRF_SESSION_KEY] = secrets.token_urlsafe(32)
    return session[CSRF_SESSION_KEY]


def wants_json():
    return request.is_json or request.accept_mimetypes.best == "application/json"


def _check_csrf():
    if request.method not in UNSAFE_METHODS or current_app.config.get("CSRF_DISABLED"):
        return None
    expected = session.get(CSRF_SESSION_KEY)
    sent = request.headers.get(CSRF_HEADER) or request.form.get(CSRF_FORM_FIELD)
    if not expected or not sent or not hmac.compare_digest(str(expected), str(sent)):
        current_app.logger.warning("CSRF check failed for %s %s", request.method, request.path)
        if wants_json():
            return jsonify(error="Your session has expired. Please reload the page."), 400
        abort(400, description="Your form session expired. Please go back, reload the page and try again.")
    return None


def _security_headers(response):
    h = response.headers
    h.setdefault("X-Content-Type-Options", "nosniff")
    h.setdefault("X-Frame-Options", "DENY")
    h.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    h.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "                                   # only our own JS files
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
    )
    if current_app.config.get("SESSION_COOKIE_SECURE"):
        h.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


class RateLimiter:
    """Sliding-window limiter kept in memory.

    Note: memory is per process. With several server workers each has its own
    counts; use Redis for a shared limit if you scale out.
    """

    def __init__(self, limit, window_seconds):
        self.limit, self.window = limit, window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key, now):
        hits = self._hits[key]
        while hits and hits[0] <= now - self.window:
            hits.popleft()
        return hits

    def is_limited(self, key):
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.limit

    def hit(self, key):
        """Record one event. Returns True if this event is over the limit."""
        with self._lock:
            now = time.monotonic()
            hits = self._recent(key, now)
            if len(hits) >= self.limit:
                return True
            hits.append(now)
            return False

    def reset(self, key):
        with self._lock:
            self._hits.pop(key, None)


def init_app(app):
    app.before_request(_check_csrf)
    app.after_request(_security_headers)
    app.jinja_env.globals["csrf_token"] = csrf_token
    # Limits (configurable): failed logins per IP+email, chat messages per admin.
    app.extensions["login_limiter"] = RateLimiter(app.config["LOGIN_MAX_FAILURES"], app.config["LOGIN_LOCKOUT_SECONDS"])
    app.extensions["chat_limiter"] = RateLimiter(app.config["CHAT_MAX_PER_MINUTE"], 60)
