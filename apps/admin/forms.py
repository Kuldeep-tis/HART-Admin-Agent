from apps.core.services import agent_service, hotel_service

MAX_ADMIN_PROMPT = 2000


def validate_agent_form(form, is_new):
    """Return (clean_data, errors). errors is empty when the form is valid."""
    data = {
        "agent_name": (form.get("agent_name") or "").strip(),
        "greeting": (form.get("greeting") or "").strip() or None,
        "admin_prompt": (form.get("admin_prompt") or "").strip() or None,
    }
    errors = {}

    if is_new:
        raw_id = (form.get("hotel_id") or "").strip()
        data["hotel_id"] = int(raw_id) if raw_id.isdigit() else None
        if data["hotel_id"] is None:
            errors["hotel_id"] = "Choose a hotel."
        elif hotel_service.get_hotel(data["hotel_id"]) is None:
            errors["hotel_id"] = "That hotel does not exist in the hotel database."
        elif agent_service.hotel_has_agent(data["hotel_id"]):
            errors["hotel_id"] = "This hotel already has an agent."

    if not data["agent_name"]:
        errors["agent_name"] = "Agent name is required."
    elif len(data["agent_name"]) > 60:
        errors["agent_name"] = "Keep the agent name under 60 characters."

    if data["greeting"] and len(data["greeting"]) > 500:
        errors["greeting"] = "Keep the greeting under 500 characters."

    if data["admin_prompt"] and len(data["admin_prompt"]) > MAX_ADMIN_PROMPT:
        errors["admin_prompt"] = f"Keep custom instructions under {MAX_ADMIN_PROMPT} characters."

    return data, errors
