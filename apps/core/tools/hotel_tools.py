"""Tools for basic hotel information (hotel_info.db: hotels, hotel_policies, room_types)."""

from apps.core.services import hotel_service
from apps.core.tools.registry import tool


@tool(
    "get_hotel_info",
    label="Looked up hotel details",
    progress="Looking up hotel details…",
    description=(
        "Hotel details: address, check-in/check-out times, phone, email, star rating. "
        "For timings, location or contact."
    ),
)
def get_hotel_info(hotel_id):
    h = hotel_service.get_hotel(hotel_id)
    if h is None:
        return {"error": "Hotel not found."}
    return {
        "name": h["name"], "address": h["address"], "city": h["city"], "star_rating": h["star_rating"],
        "check_in_time": h["check_in_time"], "check_out_time": h["check_out_time"],
        "phone": h["phone"], "email": h["email"],
    }


@tool(
    "get_policies",
    label="Checked hotel policies",
    progress="Checking hotel policies…",
    description=(
        "Hotel policies: cancellation, pets, smoking, payment. For rules, fees or what is allowed."
    ),
)
def get_policies(hotel_id):
    policies = [{"type": p["policy_type"], "policy": p["description"]} for p in hotel_service.get_policies(hotel_id)]
    return {"policies": policies, "note": None if policies else "No policies are recorded."}


@tool(
    "get_room_types",
    label="Looked up room types",
    progress="Looking up room types…",
    description=(
        "Room types: max guests, price per night (INR), total rooms. Not live availability."
    ),
)
def get_room_types(hotel_id):
    return {
        "currency": "INR",
        "room_types": [
            {"room_type": r["name"], "max_guests": r["capacity"],
             "price_per_night": r["base_price"], "total_rooms": r["total_rooms"]}
            for r in hotel_service.get_room_types(hotel_id)
        ],
    }
