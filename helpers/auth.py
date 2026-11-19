from functools import wraps

from flask import abort, flash, g, redirect, url_for

PLATFORM_ADMIN = "platform_admin"


def login_required(view):
    """Decorator: send anonymous visitors to the login page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped


def platform_admin_required(view):
    """Logged in AND a platform admin.

    Platform admins can see every hotel. Any other role (e.g. a future
    hotel_admin, who must only see their own hotel) is refused here, so a new
    role can never accidentally get access to all hotels.
    """

    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != PLATFORM_ADMIN:
            abort(403)
        return view(*args, **kwargs)

    return wrapped
