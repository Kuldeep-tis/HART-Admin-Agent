"""Chat API: validation, storage, history, tool loop and the model fallback queue."""

import pytest
from google.genai import errors as genai_errors

from apps.core.services import conversation_service
from tests.conftest import function_responses, text_response, tool_call_response


def api_error(code):
    cls = genai_errors.ServerError if code >= 500 else genai_errors.ClientError
    return cls(code, {"error": {"code": code, "message": "x", "status": "X"}})


@pytest.mark.parametrize("payload, status", [
    ({"message": ""}, 400),
    ({"message": "   "}, 400),
    ({"message": "x" * 1001}, 400),
    ({}, 400),
])
def test_message_validation(admin, agents, gemini, payload, status):
    assert admin.post(f"/dashboard/{agents[1]}/preview/chat", json=payload).status_code == status
    assert gemini.calls == []


def test_non_json_body_is_rejected(admin, agents, gemini):
    r = admin.post(f"/dashboard/{agents[1]}/preview/chat", data="hello", content_type="text/plain")
    assert r.status_code == 400


def test_unknown_agent_is_404(admin, gemini):
    assert admin.chat(999, "hi").status_code == 404


def test_conversation_is_stored_with_details(app, admin, agents, gemini):
    r = admin.chat(agents[2], "What time is check-in?").get_json()
    assert r["reply"] == "Fake answer." and r["session_id"] and r["guest_id"]
    with app.app_context():
        conv = conversation_service.find_conversation(r["session_id"], agents[2],
                                                      conversation_service.get_or_create_guest(r["guest_id"])["id"])
        msgs = conversation_service.get_messages(conv["id"])
    assert conv["hotel_id"] == 2 and conv["channel"] == "preview"
    assert [m["role"] for m in msgs] == ["assistant", "user", "assistant"]  # greeting, question, answer
    answer = msgs[-1]
    assert answer["model"] == "main-model" and answer["input_tokens"] == 100 and answer["latency_ms"] is not None


def test_follow_up_uses_history_from_the_database(admin, agents, gemini):
    first = admin.chat(agents[1], "Q1").get_json()
    admin.chat(agents[1], "Q2", session_id=first["session_id"], guest_id=first["guest_id"])
    roles_texts = [(c.role, c.parts[0].text) for c in gemini.calls[-1]["contents"]]
    assert roles_texts == [("user", "Q1"), ("model", "Fake answer."), ("user", "Q2")]


def test_failed_answer_is_stored_but_never_sent_back_to_the_ai(app, admin, agents, gemini):
    gemini.handler = lambda m, c, cfg: (_ for _ in ()).throw(RuntimeError("network down"))
    first = admin.chat(agents[1], "Q1")
    assert first.status_code == 502
    ids = {k: first.get_json()[k] for k in ("session_id", "guest_id")}

    gemini.handler = lambda m, c, cfg: text_response("ok")
    admin.chat(agents[1], "Q2", **ids)
    contents = gemini.calls[-1]["contents"]
    assert len(contents) == 1 and [p.text for p in contents[0].parts] == ["Q1", "Q2"]  # merged, no error text


def test_fallback_model_answers_when_main_is_busy(admin, agents, gemini):
    def handler(model, contents, config):
        if model == "main-model":
            raise api_error(503)
        return text_response(f"from {model}")
    gemini.handler = handler
    r = admin.chat(agents[1], "hi")
    assert r.status_code == 200 and r.get_json()["reply"] == "from backup-model"


@pytest.mark.parametrize("code", [404, 429, 500, 503])
def test_queue_moves_on_for_model_specific_errors(admin, agents, gemini, code):
    gemini.handler = lambda m, c, cfg: (_ for _ in ()).throw(api_error(code)) if m == "main-model" else text_response("ok")
    assert admin.chat(agents[1], "hi").status_code == 200


def test_bad_api_key_stops_immediately(admin, agents, gemini):
    gemini.handler = lambda m, c, cfg: (_ for _ in ()).throw(api_error(401))
    r = admin.chat(agents[1], "hi")
    assert r.status_code == 502 and "API key" in r.get_json()["error"]
    assert len(gemini.calls) == 1


def test_all_models_busy_gives_a_clear_message(admin, agents, gemini):
    gemini.handler = lambda m, c, cfg: (_ for _ in ()).throw(api_error(503))
    r = admin.chat(agents[1], "hi")
    assert r.status_code == 502 and "busy" in r.get_json()["error"]
    assert [c["model"] for c in gemini.calls] == ["main-model", "backup-model"]


def test_missing_api_key(app, admin, agents):
    app.config["GEMINI_API_KEY"] = None
    app.extensions.pop("gemini_client", None)
    r = admin.chat(agents[1], "hi")
    assert r.status_code == 502 and "GEMINI_API_KEY" in r.get_json()["error"]


def test_tool_loop_runs_tools_and_stores_them(app, admin, agents, gemini):
    def handler(model, contents, config):
        return text_response("2 guests") if function_responses(contents) else tool_call_response("get_occupancy")
    gemini.handler = handler
    r = admin.chat(agents[2], "how many guests are checked in?").get_json()
    assert r["reply"] == "2 guests" and r["tools"] == ["Checked occupancy and check-ins"]
    hist = admin.get(f"/dashboard/{agents[2]}/preview/history?session_id={r['session_id']}&guest_id={r['guest_id']}").get_json()
    assert hist["messages"][-1]["tools"] == ["Checked occupancy and check-ins"]


def test_unknown_tool_from_the_ai_is_handled(admin, agents, gemini):
    def handler(model, contents, config):
        sent = function_responses(contents)
        if not sent:
            return tool_call_response("drop_all_tables")
        assert "Unknown tool" in sent[0].response["error"]
        return text_response("sorry")
    gemini.handler = handler
    assert admin.chat(agents[1], "hi").get_json()["reply"] == "sorry"


def test_endless_tool_calls_are_stopped(admin, agents, gemini):
    gemini.handler = lambda m, c, cfg: tool_call_response("get_policies")
    r = admin.chat(agents[1], "hi")
    assert r.status_code == 502 and "too many lookups" in r.get_json()["error"]


def test_new_chat_keeps_the_guest(admin, agents, gemini):
    a = admin.chat(agents[1], "hi").get_json()
    b = admin.chat(agents[1], "hi", guest_id=a["guest_id"]).get_json()  # no session_id = "New chat"
    assert b["guest_id"] == a["guest_id"] and b["session_id"] != a["session_id"]
