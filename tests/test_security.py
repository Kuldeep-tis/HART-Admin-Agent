"""Authentication, authorisation, CSRF, headers and abuse limits."""

import pytest

from app import create_app
from tests.conftest import PASSWORD, Browser

PROTECTED_GET = ["/dashboard", "/agents/new", "/dashboard/1/", "/dashboard/1/preview/",
                 "/dashboard/1/preview/history", "/dashboard/1/conversations/1/"]


@pytest.mark.parametrize("url", PROTECTED_GET)
def test_pages_require_login(browser, agents, url):
    r = browser.get(url)
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


def test_api_requires_login(browser, agents):
    r = browser.chat(agents[1], "hi")
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")


@pytest.mark.parametrize("url", PROTECTED_GET)
def test_other_roles_are_refused(app, make_user, agents, url):
    uid = make_user(email="hotel@test.io", role="hotel_admin")
    b = Browser(app.test_client())
    with b.client.session_transaction() as s:
        s["user_id"] = uid
    assert b.get(url).status_code == 403


def test_login_success_starts_a_fresh_session(browser, admin_id):
    with browser.client.session_transaction() as s:
        s["planted"] = "attacker-value"
    r = browser.login("admin@test.io")
    assert r.status_code == 302 and r.headers["Location"].endswith("/dashboard")
    with browser.client.session_transaction() as s:
        assert "planted" not in s and s["user_id"] == admin_id
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie


def test_wrong_password_and_unknown_email_look_the_same(browser, admin_id):
    a = browser.login("admin@test.io", "wrong-password")
    b = browser.login("nobody@test.io", "wrong-password")
    assert a.status_code == b.status_code == 200
    assert b"Invalid email or password." in a.data and b"Invalid email or password." in b.data


def test_login_is_locked_after_repeated_failures(app, browser, admin_id):
    for _ in range(app.config["LOGIN_MAX_FAILURES"]):
        browser.login("admin@test.io", "wrong-password")
    r = browser.login("admin@test.io")  # even the right password is refused while locked
    assert r.status_code == 429
    with browser.client.session_transaction() as s:
        assert "user_id" not in s


def test_forms_without_csrf_token_are_rejected(admin, agents):
    r = admin.post(f"/agents/{agents[1]}/toggle-status", csrf=False)
    assert r.status_code == 400
    assert admin.post(f"/agents/{agents[1]}/toggle-status").status_code == 302


def test_chat_without_csrf_token_is_rejected(admin, agents, gemini):
    r = admin.post(f"/dashboard/{agents[1]}/preview/chat", csrf=False, json={"message": "hi"})
    assert r.status_code == 400 and "error" in r.get_json()
    assert gemini.calls == []


def test_login_form_needs_csrf_token(browser, admin_id):
    r = browser.post("/login", csrf=False, data={"email": "admin@test.io", "password": PASSWORD})
    assert r.status_code == 400


def test_logout_only_by_post(admin):
    assert admin.get("/logout").status_code == 405
    assert admin.post("/logout").status_code == 302


def test_security_headers(browser):
    h = browser.get("/").headers
    assert "script-src 'self'" in h["Content-Security-Policy"]
    assert h["X-Frame-Options"] == "DENY"
    assert h["X-Content-Type-Options"] == "nosniff"
    assert "microphone=()" in h["Permissions-Policy"]   # blocked by default


def test_only_the_chat_preview_may_use_the_microphone(admin, agents):
    preview = admin.get(f"/dashboard/{agents[2]}/preview/").headers["Permissions-Policy"]
    assert "microphone=(self)" in preview and "camera=()" in preview   # mic button, own site only
    other = admin.get(f"/dashboard/{agents[2]}/").headers["Permissions-Policy"]
    assert "microphone=()" in other


def test_pages_embed_csrf_token(admin, agents):
    html = admin.get(f"/dashboard/{agents[1]}/").get_data(as_text=True)
    assert 'name="csrf-token"' in html and 'name="csrf_token"' in html


def test_production_refuses_to_start_without_secret_key():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app({"SECRET_KEY": None})


def test_oversized_request_is_rejected(admin, agents):
    r = admin.post(f"/dashboard/{agents[1]}/preview/chat", json={"message": "x" * 100_000})
    assert r.status_code == 413


def test_unknown_page_is_a_friendly_404(admin):
    r = admin.get("/does-not-exist")
    assert r.status_code == 404 and b"Go back home" in r.data


def test_chat_rate_limit(app, admin, agents, gemini):
    app.extensions["chat_limiter"].limit = 3
    codes = [admin.chat(agents[1], f"q{i}").status_code for i in range(5)]
    assert codes[:3] == [200, 200, 200] and codes[3:] == [429, 429]


def test_user_html_is_escaped(admin, agents, gemini):
    admin.post(f"/agents/{agents[1]}/settings",
               data={"agent_name": "<script>alert(1)</script>", "greeting": "", "admin_prompt": ""})
    html = admin.get(f"/dashboard/{agents[1]}/").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html
