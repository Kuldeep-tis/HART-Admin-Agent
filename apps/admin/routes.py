from flask import (
    Blueprint, abort, flash, g, redirect, render_template, request, session, url_for,
)
from werkzeug.security import check_password_hash

from apps.admin import filters
from apps.admin.forms import validate_agent_form
from apps.core.services import agent_service, hotel_service, log_service, user_service
from helpers.auth import login_required
from helpers.database import HotelDatabaseMissing

admin_bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/admin-static",
)
filters.register(admin_bp)


@admin_bp.before_app_request
def load_logged_in_user():
    """Runs before every request: put the logged-in user (or None) on g.user."""
    user_id = session.get("user_id")
    g.user = user_service.get_user_by_id(user_id) if user_id else None


@admin_bp.app_errorhandler(HotelDatabaseMissing)
def hotel_db_missing(error):
    return render_template("hotel_db_missing.html", path=str(error)), 503


@admin_bp.route("/favicon.ico")
def favicon():
    """Browsers request /favicon.ico directly; point them at the SVG icon."""
    return redirect(url_for("admin.static", filename="img/favicon.svg"), code=301)


# ---------- Public pages ----------

@admin_bp.route("/")
def landing():
    return render_template("landing.html")


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("admin.dashboard"))

    email = ""
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = user_service.get_user_by_email(email)

        # Same message for both cases, so attackers can't learn which emails exist.
        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid email or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            log_service.log("login", user_id=user["id"])
            flash(f"Welcome back, {user['name']}!", "success")
            return redirect(url_for("admin.dashboard"))

    return render_template("login.html", email=email)


@admin_bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        log_service.log("logout", user_id=g.user["id"])
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("admin.landing"))


# ---------- Dashboard + agents ----------

@admin_bp.route("/dashboard")
@login_required
def dashboard():
    counts = agent_service.status_counts()
    counts["unconfigured"] = len(agent_service.unconfigured_hotels())
    return render_template(
        "dashboard.html",
        rows=agent_service.list_agents_with_hotels(),
        counts=counts,
        activity=log_service.recent(15),
    )


@admin_bp.route("/dashboard/<int:agent_id>/")
@login_required
def agent_detail(agent_id):
    agent = agent_service.get_agent(agent_id)
    if agent is None:
        abort(404)

    hotel = hotel_service.get_hotel(agent["hotel_id"])
    context = {
        "agent": agent,
        "hotel": hotel,
        "creator": user_service.get_user_by_id(agent["created_by"]) if agent["created_by"] else None,
        "activity": log_service.for_agent(agent_id),
    }
    if hotel:
        context.update(
            policies=hotel_service.get_policies(hotel["id"]),
            room_types=hotel_service.get_room_types_with_bookings(hotel["id"]),
            stats=hotel_service.get_booking_stats(hotel["id"]),
            bookings=hotel_service.get_recent_bookings(hotel["id"]),
        )
    return render_template("agent_detail.html", **context)


@admin_bp.route("/agents/new", methods=["GET", "POST"])
@login_required
def agent_new():
    values, errors = {}, {}
    if request.method == "POST":
        values, errors = validate_agent_form(request.form, is_new=True)
        if not errors:
            agent_id = agent_service.create_agent(values, created_by=g.user["id"])
            hotel = hotel_service.get_hotel(values["hotel_id"])
            log_service.log("agent_created", g.user["id"], agent_id, f"{values['agent_name']} for {hotel['name']}")
            flash(f"Agent {values['agent_name']} created for {hotel['name']}.", "success")
            return redirect(url_for("admin.dashboard"))

    return render_template(
        "agent_form.html",
        mode="new",
        hotels=agent_service.unconfigured_hotels(),
        values=values,
        errors=errors,
    )


@admin_bp.route("/agents/<int:agent_id>/edit", methods=["GET", "POST"])
@login_required
def agent_edit(agent_id):
    agent = agent_service.get_agent(agent_id)
    if agent is None:
        abort(404)
    hotel = hotel_service.get_hotel(agent["hotel_id"])

    values, errors = dict(agent), {}
    if request.method == "POST":
        values, errors = validate_agent_form(request.form, is_new=False)
        if not errors:
            agent_service.update_agent(agent_id, values)
            log_service.log("agent_updated", g.user["id"], agent_id, values["agent_name"])
            flash(f"Agent {values['agent_name']} updated.", "success")
            return redirect(url_for("admin.agent_detail", agent_id=agent_id))

    return render_template(
        "agent_form.html", mode="edit", agent=agent, hotel=hotel, values=values, errors=errors
    )


@admin_bp.route("/agents/<int:agent_id>/toggle-status", methods=["POST"])
@login_required
def agent_toggle_status(agent_id):
    agent = agent_service.get_agent(agent_id)
    if agent is None:
        abort(404)
    agent_service.toggle_status(agent_id)
    new_status = "inactive" if agent["status"] == "active" else "active"
    log_service.log("agent_status_changed", g.user["id"], agent_id, f"{agent['agent_name']} → {new_status}")
    flash(f"Agent {agent['agent_name']} is now {new_status}.", "success")
    if request.form.get("return_to") == "detail":
        return redirect(url_for("admin.agent_detail", agent_id=agent_id))
    return redirect(url_for("admin.dashboard"))
