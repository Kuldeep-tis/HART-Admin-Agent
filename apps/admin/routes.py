import json
import time

from flask import (
    Blueprint, Response, abort, current_app, flash, g, jsonify, redirect, render_template, request, session,
    stream_with_context, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from apps.admin import filters
from apps.admin.forms import validate_agent_form
from apps.core import agent as hotel_agent
from apps.core import tools as hotel_tools
from apps.core.services import (
    agent_service, conversation_service, hotel_service, log_service, user_service,
)
from helpers.auth import platform_admin_required
from helpers.database import HotelDatabaseMissing
from helpers.security import wants_json

admin_bp = Blueprint(
    "admin",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/admin-static",
)
filters.register(admin_bp)

# Used to keep login timing the same for unknown emails (see login()).
_DUMMY_HASH = generate_password_hash("not-a-real-password")


@admin_bp.before_app_request
def load_logged_in_user():
    """Runs before every request: put the logged-in user (or None) on g.user."""
    user_id = session.get("user_id")
    g.user = user_service.get_user_by_id(user_id) if user_id else None


@admin_bp.app_errorhandler(HotelDatabaseMissing)
def hotel_db_missing(error):
    if wants_json():
        return jsonify(error="The hotel database is not available."), 503
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
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")
        limiter = current_app.extensions["login_limiter"]
        limit_key = f"{request.remote_addr}|{email}"

        if limiter.is_limited(limit_key):
            current_app.logger.warning("Login locked out for %s", limit_key)
            flash("Too many failed attempts. Please wait 15 minutes and try again.", "error")
            return render_template("login.html", email=email), 429

        user = user_service.get_user_by_email(email)
        # Always run one password hash check, even for unknown emails, so the response
        # time doesn't reveal which emails exist. Same message for both cases.
        password_ok = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
        if user is None or not password_ok:
            limiter.hit(limit_key)
            flash("Invalid email or password.", "error")
        else:
            limiter.reset(limit_key)
            session.clear()             # new session on login (prevents session fixation)
            session.permanent = True    # expires after PERMANENT_SESSION_LIFETIME
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
@platform_admin_required
def dashboard():
    counts = agent_service.status_counts()
    counts["unconfigured"] = len(agent_service.unconfigured_hotels())
    return render_template(
        "dashboard.html",
        rows=agent_service.list_agents_with_hotels(),
        counts=counts,
        activity=log_service.recent(50),
    )


def _get_agent_or_404(agent_id):
    agent = agent_service.get_agent(agent_id)
    if agent is None:
        abort(404)
    return agent


def _render_agent_detail(agent, values=None, errors=None):
    """Build the detail page. Used by the page itself and when a settings save fails."""
    hotel = hotel_service.get_hotel(agent["hotel_id"])
    context = {
        "agent": agent,
        "hotel": hotel,
        "creator": user_service.get_user_by_id(agent["created_by"]) if agent["created_by"] else None,
        "activity": log_service.for_agent(agent["id"]),
        "conversations": conversation_service.list_for_agent(agent["id"]),
        "chat_stats": conversation_service.stats_for_agent(agent["id"]),
        # Editable settings form: saved values, or what the user typed if validation failed.
        "values": values if values is not None else dict(agent),
        "errors": errors or {},
    }
    # Exactly what Gemini will read, so the admin can check it.
    try:
        context["system_prompt"] = hotel_agent.system_prompt_for(agent)
        context["agent_tools"] = list(hotel_tools.TOOLS.values())
    except hotel_agent.AgentError:
        context["system_prompt"] = None
    if hotel:
        context.update(
            policies=hotel_service.get_policies(hotel["id"]),
            room_types=hotel_service.get_room_types_with_bookings(hotel["id"]),
            stats=hotel_service.get_booking_stats(hotel["id"]),
            bookings=hotel_service.get_recent_bookings(hotel["id"]),
        )
    return render_template("agent_detail.html", **context)


@admin_bp.route("/dashboard/<int:agent_id>/")
@platform_admin_required
def agent_detail(agent_id):
    return _render_agent_detail(_get_agent_or_404(agent_id))


# ---------- Preview chat ----------

MAX_MESSAGE_LENGTH = 1000
MAX_HISTORY_TURNS = 20


@admin_bp.route("/dashboard/<int:agent_id>/preview/")
@platform_admin_required
def agent_preview(agent_id):
    agent = _get_agent_or_404(agent_id)
    hotel = hotel_service.get_hotel(agent["hotel_id"])
    rooms = hotel_service.get_room_types(agent["hotel_id"]) if hotel else []
    hotel_agent.warm_up(agent)   # local model: pre-read prompt + tools while the page loads
    return render_template(
        "preview.html", agent=agent, hotel=hotel, rooms=rooms,
        greeting=hotel_agent.greeting_for(agent, hotel),
    )


@admin_bp.route("/dashboard/<int:agent_id>/preview/chat", methods=["POST"])
@platform_admin_required
def agent_preview_chat(agent_id):
    """JSON API for the chat widget.

    Request:  {message, session_id?, guest_id?, stream?}
    Response: {reply, session_id, guest_id}   (or {error, session_id, guest_id})
              With "stream": true, the reply is sent word by word instead (see _stream_reply).

    The conversation history is read from the DATABASE, never from the browser.
    """
    agent = _get_agent_or_404(agent_id)
    data = request.get_json(silent=True) or {}

    if current_app.extensions["chat_limiter"].hit(f"user:{g.user['id']}"):
        return jsonify(error="You're sending messages too quickly. Please wait a minute."), 429

    message = str(data.get("message") or "").strip()
    if not message:
        return jsonify(error="Message cannot be empty."), 400
    if len(message) > MAX_MESSAGE_LENGTH:
        return jsonify(error=f"Keep messages under {MAX_MESSAGE_LENGTH} characters."), 400

    # 1. Who is chatting (browser id) and which session (continue or start new).
    guest = conversation_service.get_or_create_guest(data.get("guest_id"))
    conversation = conversation_service.find_conversation(data.get("session_id"), agent_id, guest["id"])
    if conversation is None:
        hotel = hotel_service.get_hotel(agent["hotel_id"])
        conversation = conversation_service.start_conversation(
            agent, guest["id"], channel="preview", started_by=g.user["id"],
            greeting=hotel_agent.greeting_for(agent, hotel),
        )
    ids = {"session_id": conversation["session_id"], "guest_id": guest["public_id"]}

    # 2. Earlier turns from the DB, then store the new question.
    history = conversation_service.get_history(conversation["id"], MAX_HISTORY_TURNS)
    conversation_service.add_message(conversation["id"], "user", message)

    if data.get("stream"):
        return _stream_reply(agent, conversation, message, history, ids)

    # 3. Ask the agent and store the answer (or the failure).
    started = time.perf_counter()
    try:
        reply = hotel_agent.respond(agent, message, history)
    except hotel_agent.AgentError as exc:
        conversation_service.add_message(
            conversation["id"], "assistant", str(exc), status="error",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return jsonify(error=str(exc), **ids), 502

    conversation_service.add_message(
        conversation["id"], "assistant", reply.text, model=reply.model,
        input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
        latency_ms=int((time.perf_counter() - started) * 1000), tool_calls=reply.tool_calls,
    )
    used = [hotel_tools.TOOLS[c["name"]].label for c in reply.tool_calls if c["name"] in hotel_tools.TOOLS]
    return jsonify(reply=reply.text, tools=list(dict.fromkeys(used)), **ids)


def _stream_reply(agent, conversation, message, history, ids):
    """Send the answer while it is being written, as NDJSON (one JSON object per line):

        {"type": "token", "text": "Check-in"}           more words
        {"type": "tool", "label": "Checked hotel info"}  a database lookup is running
        {"type": "reset"}                                clear the words shown so far
        {"type": "done", "tools": [...], "session_id": ..., "guest_id": ...}
        {"type": "error", "error": "...", "session_id": ..., "guest_id": ...}

    The answer (or the failure) is stored in the database at the end, like the JSON mode.
    """
    def line(obj):
        return json.dumps(obj) + "\n"

    def ms_since(start):
        return int((time.perf_counter() - start) * 1000)

    def events():
        started = time.perf_counter()
        first_token_ms = None   # when the guest saw the first words
        reply = None
        try:
            for event in hotel_agent.respond_stream(agent, message, history):
                if event["type"] == "final":
                    reply = event["reply"]
                elif event["type"] == "tool":
                    tool = hotel_tools.TOOLS.get(event["name"])
                    yield line({"type": "tool", "label": tool.label if tool else "Looked up hotel data"})
                else:
                    if event["type"] == "token" and first_token_ms is None:
                        first_token_ms = ms_since(started)
                    yield line(event)   # token / reset
        except GeneratorExit:
            # The browser stopped listening (page refreshed or closed, network dropped).
            # Store that, so the question doesn't sit in the transcript without any reply.
            conversation_service.add_message(
                conversation["id"], "assistant", "The reply was cancelled: the chat page was closed or refreshed.",
                status="error", latency_ms=ms_since(started), first_token_ms=first_token_ms,
            )
            raise
        except Exception as exc:  # AgentError, or a bug: the stream has started, so report it as an event
            if isinstance(exc, hotel_agent.AgentError):
                error = str(exc)
            else:
                current_app.logger.exception("Streaming reply failed for agent %s", agent["id"])
                error = "Something went wrong on the server. Please try again."
            conversation_service.add_message(
                conversation["id"], "assistant", error, status="error",
                latency_ms=ms_since(started), first_token_ms=first_token_ms,
            )
            yield line({"type": "error", "error": error, **ids})
            return

        conversation_service.add_message(
            conversation["id"], "assistant", reply.text, model=reply.model,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens,
            latency_ms=ms_since(started), tool_calls=reply.tool_calls, first_token_ms=first_token_ms,
        )
        used = [hotel_tools.TOOLS[c["name"]].label for c in reply.tool_calls if c["name"] in hotel_tools.TOOLS]
        yield line({"type": "done", "tools": list(dict.fromkeys(used)), **ids})

    # stream_with_context: keeps the request (login, database connection) available while streaming.
    # X-Accel-Buffering: tells nginx not to hold the lines back until the end.
    return Response(
        stream_with_context(events()), mimetype="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _tool_labels(tool_calls_json):
    """Stored JSON -> unique human labels, e.g. ["Checked room availability"]."""
    if not tool_calls_json:
        return []
    names = [c.get("name") for c in json.loads(tool_calls_json)]
    return list(dict.fromkeys(hotel_tools.TOOLS[n].label for n in names if n in hotel_tools.TOOLS))


@admin_bp.route("/dashboard/<int:agent_id>/preview/history")
@platform_admin_required
def agent_preview_history(agent_id):
    """Messages of a session, so the chat survives a page refresh."""
    _get_agent_or_404(agent_id)
    guest = conversation_service.get_or_create_guest(request.args.get("guest_id"))
    conversation = conversation_service.find_conversation(request.args.get("session_id"), agent_id, guest["id"])
    if conversation is None:
        return jsonify(error="Conversation not found."), 404
    messages = [
        {"role": m["role"], "content": m["content"], "status": m["status"], "created_at": m["created_at"],
         "tools": _tool_labels(m["tool_calls"])}
        for m in conversation_service.get_messages(conversation["id"])
    ]
    return jsonify(session_id=conversation["session_id"], messages=messages)


@admin_bp.route("/dashboard/<int:agent_id>/conversations/<int:conversation_id>/")
@platform_admin_required
def conversation_detail(agent_id, conversation_id):
    agent = _get_agent_or_404(agent_id)
    conversation = conversation_service.get_conversation(conversation_id)
    if conversation is None or conversation["agent_id"] != agent_id:
        abort(404)  # never show another agent's conversation
    messages = conversation_service.get_messages(conversation_id)
    answers = [m for m in messages if m["role"] == "assistant" and m["latency_ms"] is not None]
    streamed = [m for m in answers if m["first_token_ms"] is not None]
    totals = {
        "tokens": sum((m["input_tokens"] or 0) + (m["output_tokens"] or 0) for m in messages),
        "avg_latency_ms": int(sum(m["latency_ms"] for m in answers) / len(answers)) if answers else None,
        "avg_first_token_ms": int(sum(m["first_token_ms"] for m in streamed) / len(streamed)) if streamed else None,
        "errors": sum(1 for m in messages if m["status"] == "error"),
    }
    return render_template(
        "conversation.html", agent=agent, hotel=hotel_service.get_hotel(agent["hotel_id"]),
        conversation=conversation, messages=messages, totals=totals,
    )


@admin_bp.route("/agents/new", methods=["GET", "POST"])
@platform_admin_required
def agent_new():
    values, errors = {}, {}
    if request.method == "POST":
        values, errors = validate_agent_form(request.form, is_new=True)
        if not errors:
            agent_id = agent_service.create_agent(values, created_by=g.user["id"])
            hotel = hotel_service.get_hotel(values["hotel_id"])
            log_service.log("agent_created", g.user["id"], agent_id, f"{values['agent_name']} for {hotel['name']}")
            flash(f"Agent {values['agent_name']} created for {hotel['name']}.", "success")
            return redirect(url_for("admin.agent_detail", agent_id=agent_id))

    return render_template(
        "agent_form.html",
        hotels=agent_service.unconfigured_hotels(),
        values=values,
        errors=errors,
    )


@admin_bp.route("/agents/<int:agent_id>/settings", methods=["POST"])
@platform_admin_required
def agent_update_settings(agent_id):
    """Save the editable agent settings shown on the detail page."""
    agent = _get_agent_or_404(agent_id)
    values, errors = validate_agent_form(request.form, is_new=False)

    if errors:
        flash("Please fix the highlighted fields.", "error")
        return _render_agent_detail(agent, values=values, errors=errors)

    if all(values[f] == agent[f] for f in ("agent_name", "greeting", "admin_prompt")):
        flash("No changes to save.", "info")
    else:
        agent_service.update_agent(agent_id, values)
        log_service.log("agent_updated", g.user["id"], agent_id, values["agent_name"])
        flash(f"Settings for {values['agent_name']} saved.", "success")
    return redirect(url_for("admin.agent_detail", agent_id=agent_id))


@admin_bp.route("/agents/<int:agent_id>/toggle-status", methods=["POST"])
@platform_admin_required
def agent_toggle_status(agent_id):
    agent = _get_agent_or_404(agent_id)
    agent_service.toggle_status(agent_id)
    new_status = "inactive" if agent["status"] == "active" else "active"
    log_service.log("agent_status_changed", g.user["id"], agent_id, f"{agent['agent_name']} → {new_status}")
    flash(f"Agent {agent['agent_name']} is now {new_status}.", "success")
    if request.form.get("return_to") == "detail":
        return redirect(url_for("admin.agent_detail", agent_id=agent_id))
    return redirect(url_for("admin.dashboard"))
