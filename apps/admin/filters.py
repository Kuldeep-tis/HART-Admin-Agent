"""Jinja filters: small helpers that format values inside templates.

Usage in a template:  {{ item.created_at | timeago }}
"""

from datetime import datetime, timezone


def _parse_utc(value):
    # SQLite CURRENT_TIMESTAMP is stored as UTC text: "2026-11-19 09:30:21"
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


def timeago(value):
    if not value:
        return ""
    seconds = int((datetime.now(timezone.utc) - _parse_utc(value)).total_seconds())
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} hr ago"
    days = seconds // 86400
    if days < 7:
        return f"{days} day{'s' if days > 1 else ''} ago"
    return localtime(value, "%d %b %Y")


def localtime(value, fmt="%d %b %Y, %H:%M"):
    """UTC timestamp -> this computer's local time."""
    return _parse_utc(value).astimezone().strftime(fmt) if value else ""


def date_fmt(value):
    return datetime.strptime(value, "%Y-%m-%d").strftime("%d %b %Y") if value else ""


def money(value):
    return f"₹{value or 0:,.0f}"


def initials(name):
    return "".join(part[0] for part in (name or "?").split()[:2]).upper()


def register(blueprint):
    for f in (timeago, localtime, date_fmt, money, initials):
        blueprint.add_app_template_filter(f)
