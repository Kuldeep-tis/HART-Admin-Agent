from functools import wraps

from flask import flash, g, redirect, url_for


def login_required(view):
    """Decorator: send anonymous visitors to the login page."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            flash("Please log in first.", "error")
            return redirect(url_for("admin.login"))
        return view(*args, **kwargs)

    return wrapped
