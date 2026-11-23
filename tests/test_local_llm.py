"""Local model (Qwen via Ollama): tool loop, security, errors and the streamed chat.

A fake Ollama replaces the real HTTP call, so these tests need no model and run in seconds.
"""

import json

import pytest
import requests

from apps.core.models import qwen
from apps.core.services import conversation_service


# ---------- fake Ollama ----------

class FakeResponse:
    def __init__(self, status_code=200, chunks=()):
        self.status_code = status_code
        self.text = ""
        self._lines = [json.dumps(c).encode() for c in chunks]

    def iter_lines(self):
        yield from self._lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def words(*pieces, prompt_tokens=100, output_tokens=10):
    """Ollama's streamed text answer: one chunk per piece, then the final 'done' chunk."""
    chunks = [{"message": {"role": "assistant", "content": p}, "done": False} for p in pieces]
    chunks.append({"message": {"role": "assistant", "content": ""}, "done": True,
                   "prompt_eval_count": prompt_tokens, "eval_count": output_tokens})
    return chunks


def tool_request(name, args=None, text=""):
    """Ollama asking us to run a tool."""
    return [
        {"message": {"role": "assistant", "content": text,
                     "tool_calls": [{"function": {"name": name, "arguments": args or {}}}]}, "done": False},
        {"message": {"role": "assistant", "content": ""}, "done": True, "prompt_eval_count": 50, "eval_count": 5},
    ]


class FakeOllama:
    """Plays back one scripted reply per call and records what we sent."""

    def __init__(self):
        self.replies = [words("Fake ", "local ", "answer.")]
        self.payloads = []

    def post(self, url, json=None, timeout=None, stream=False):
        self.payloads.append(json)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, FakeResponse):
            return reply
        return FakeResponse(200, reply)


@pytest.fixture
def ollama(app, monkeypatch):
    fake = FakeOllama()
    monkeypatch.setattr(qwen.requests, "post", fake.post)
    monkeypatch.setattr(qwen, "_warmed_at", {})   # each test starts with nothing warmed
    app.config["LLM_PROVIDER"] = "local"
    return fake


def ask(**overrides):
    kwargs = dict(system_prompt="sys", history=[], message="hi", hotel_id=2,
                  url="http://ollama", model="qwen-test", timeout=5)
    kwargs.update(overrides)
    return qwen.answer(**kwargs)


def stream_chat(admin, agent_id, message, **ids):
    """Send a streamed chat message; return (response, list of NDJSON events)."""
    r = admin.post(f"/dashboard/{agent_id}/preview/chat", json={"message": message, "stream": True, **ids})
    events = [json.loads(line) for line in r.get_data(as_text=True).splitlines() if line.strip()]
    return r, events


# ---------- qwen.py ----------

def test_text_answer_and_request_settings(app, ollama):
    with app.app_context():
        reply = ask()
    assert reply.text == "Fake local answer." and reply.model == "qwen-test"
    assert reply.input_tokens == 100 and reply.output_tokens == 10
    sent = ollama.payloads[0]
    assert sent["think"] is False and sent["stream"] is True
    assert sent["messages"][0] == {"role": "system", "content": "sys"}


def test_model_never_sees_hotel_id(app, ollama):
    with app.app_context():
        ask()
    for t in ollama.payloads[0]["tools"]:
        assert "hotel_id" not in json.dumps(t["function"]["parameters"])


def test_tool_runs_for_our_hotel_even_if_the_model_asks_for_another(app, ollama):
    # The model tries to sneak in hotel_id=1; execute() must ignore it and use OUR hotel (2).
    ollama.replies = [tool_request("get_room_types", {"hotel_id": 1}), words("Rooms listed.")]
    with app.app_context():
        reply = ask(hotel_id=2)
    rooms = reply.tool_calls[0]["result"]
    names = json.dumps(rooms)
    assert "Sea View" in names and "Deluxe Room" not in names   # Seaside rooms only, not Grand Palace
    tool_msg = ollama.payloads[1]["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_name"] == "get_room_types"


def test_words_before_a_tool_call_are_reset(app, ollama):
    ollama.replies = [tool_request("get_hotel_info", text="Let me check..."), words("Check-in is 2 PM.")]
    with app.app_context():
        events = list(qwen.stream_answer(system_prompt="s", history=[], message="m", hotel_id=2,
                                         url="u", model="m", timeout=5))
    types = [e["type"] for e in events]
    assert types.index("reset") < types.index("tool") < types.index("final")
    assert events[-1]["reply"].text == "Check-in is 2 PM."


def test_too_many_tool_rounds_stops(app, ollama):
    ollama.replies = [tool_request("get_hotel_info")]   # asks for a tool forever
    with app.app_context(), pytest.raises(qwen.QwenError, match="too many lookups"):
        ask()


@pytest.mark.parametrize("reply, message", [
    (requests.ConnectionError(), "not running"),
    (requests.Timeout(), "took too long"),
    (FakeResponse(404), "ollama pull qwen-test"),
    (FakeResponse(500), "returned an error"),
    ([{"error": "out of memory"}], "returned an error"),
    (words(""), "empty reply"),
])
def test_errors_become_friendly_messages(app, ollama, reply, message):
    ollama.replies = [reply]
    with app.app_context(), pytest.raises(qwen.QwenError, match=message):
        ask()


def test_think_tags_are_removed(app, ollama):
    ollama.replies = [words("<think>hmm</think>", "Hello!")]
    with app.app_context():
        assert ask().text == "Hello!"


# ---------- the app with LLM_PROVIDER=local ----------

def test_json_chat_uses_local_model(admin, agents, ollama):
    r = admin.chat(agents[2], "hi").get_json()
    assert r["reply"] == "Fake local answer."


def test_unknown_provider_is_a_clear_error(app, admin, agents):
    app.config["LLM_PROVIDER"] = "qwen"
    r = admin.chat(agents[2], "hi")
    assert r.status_code == 502 and "Unknown LLM_PROVIDER" in r.get_json()["error"]


def test_streamed_chat_sends_words_and_stores_timings(app, admin, agents, ollama):
    ollama.replies = [tool_request("get_room_types"), words("We have ", "three room types.")]
    r, events = stream_chat(admin, agents[2], "Which rooms?")
    assert r.mimetype == "application/x-ndjson"
    assert [e["type"] for e in events] == ["tool", "token", "token", "done"]
    assert events[0]["label"] and events[-1]["tools"] and events[-1]["session_id"]
    assert "".join(e["text"] for e in events if e["type"] == "token") == "We have three room types."

    with app.app_context():
        conv = conversation_service.find_conversation(
            events[-1]["session_id"], agents[2],
            conversation_service.get_or_create_guest(events[-1]["guest_id"])["id"])
        answer = conversation_service.get_messages(conv["id"])[-1]
    assert answer["content"] == "We have three room types." and answer["status"] == "ok"
    assert answer["first_token_ms"] is not None and answer["latency_ms"] >= answer["first_token_ms"]
    assert answer["tool_calls"]


def test_streamed_error_is_sent_and_stored(app, admin, agents, ollama):
    ollama.replies = [requests.ConnectionError()]
    _, events = stream_chat(admin, agents[2], "hi")
    assert events[-1]["type"] == "error" and "not running" in events[-1]["error"]
    with app.app_context():
        conv = conversation_service.find_conversation(
            events[-1]["session_id"], agents[2],
            conversation_service.get_or_create_guest(events[-1]["guest_id"])["id"])
        assert conversation_service.get_messages(conv["id"])[-1]["status"] == "error"


def test_closing_the_page_mid_answer_stores_a_cancelled_reply(app, admin, agents, ollama):
    r = admin.client.post(
        f"/dashboard/{agents[2]}/preview/chat", buffered=False,
        json={"message": "hi", "stream": True}, headers={"X-CSRF-Token": admin.csrf()},
    )
    first = next(iter(r.response))          # the first words arrive...
    assert json.loads(first)["type"] == "token"
    r.close()                               # ...then the browser goes away
    with app.app_context():
        from helpers.database import get_platform_db
        last = get_platform_db().execute("SELECT * FROM messages ORDER BY id DESC LIMIT 1").fetchone()
    assert last["role"] == "assistant" and last["status"] == "error" and "cancelled" in last["content"]


def test_streamed_chat_with_gemini_sends_whole_answer(admin, agents, gemini):
    _, events = stream_chat(admin, agents[1], "hi")
    assert [e["type"] for e in events] == ["token", "done"] and events[0]["text"] == "Fake answer."


def test_conversation_page_shows_first_words_time(app, admin, agents, ollama):
    _, events = stream_chat(admin, agents[2], "hi")
    with app.app_context():
        conv = conversation_service.find_conversation(
            events[-1]["session_id"], agents[2],
            conversation_service.get_or_create_guest(events[-1]["guest_id"])["id"])
    page = admin.get(f"/dashboard/{agents[2]}/conversations/{conv['id']}/").get_data(as_text=True)
    assert "first words" in page and "Avg. first words" in page


# ---------- speed settings: history, num_ctx, warm-up ----------

def test_only_recent_history_is_sent(app, admin, agents, ollama):
    app.config["OLLAMA_MAX_HISTORY"] = 2
    first = admin.chat(agents[2], "Q1").get_json()
    ids = {"session_id": first["session_id"], "guest_id": first["guest_id"]}
    admin.chat(agents[2], "Q2", **ids)
    admin.chat(agents[2], "Q3", **ids)
    sent = [m["content"] for m in ollama.payloads[-1]["messages"][1:]]   # skip the system prompt
    assert sent == ["Q2", "Fake local answer.", "Q3"]                    # last 2 history messages + new one


def test_num_ctx_is_sent(app, admin, agents, ollama):
    app.config["OLLAMA_NUM_CTX"] = 8192
    admin.chat(agents[2], "hi")
    assert ollama.payloads[-1]["options"]["num_ctx"] == 8192


def test_warm_up_matches_the_real_chat_request(app, ollama):
    # Same tools and options, or Ollama's cache can't be reused (or the model reloads).
    with app.app_context():
        qwen.warm_up(system_prompt="sys", url="u", model="m", num_ctx=4096)
        ask(system_prompt="sys", model="m")
    warm, real = ollama.payloads
    assert warm["tools"] == real["tools"] and warm["messages"][0] == real["messages"][0]
    assert warm["options"]["num_ctx"] == real["options"]["num_ctx"] and warm["options"]["num_predict"] == 1


def test_same_prompt_is_not_warmed_twice(ollama):
    # Refreshing the preview page must not queue warm-ups in front of real questions.
    for _ in range(3):
        qwen.warm_up(system_prompt="sys", url="u", model="m")
    qwen.warm_up(system_prompt="other agent", url="u", model="m")
    assert len(ollama.payloads) == 2


def test_warm_up_never_raises(ollama):
    ollama.replies = [requests.ConnectionError()]
    qwen.warm_up(system_prompt="s", url="u", model="m")   # no exception


@pytest.mark.parametrize("provider, warmed", [("local", True), ("gemini", False)])
def test_preview_page_warms_up_only_for_local(app, admin, agents, monkeypatch, provider, warmed):
    calls = []
    monkeypatch.setattr(qwen, "warm_up", lambda **kw: calls.append(kw))
    app.config["LLM_PROVIDER"] = provider
    assert admin.get(f"/dashboard/{agents[2]}/preview/").status_code == 200
    import time
    for _ in range(50):          # the warm-up runs in a background thread
        if calls:
            break
        time.sleep(0.01)
    assert bool(calls) is warmed
