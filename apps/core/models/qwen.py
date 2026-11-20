"""Local Qwen model, running on our own machine through Ollama.

Why local: guest data (names, bookings, payments) never leaves our server.

Ollama is a background service (like MySQL). We talk to it over HTTP:
    POST {OLLAMA_URL}/api/chat   ->   the model's reply (text, or a request to run tools)

How one answer is produced (the "tool loop", same idea as with Gemini):
  1. Send Qwen: system prompt + conversation + list of tools.
  2. Qwen either answers with text (done), or asks to call tools.
  3. We run the tools on hotel_info.db FOR THIS AGENT'S HOTEL ONLY, add the
     results to the conversation, and go to step 2. At most MAX_TOOL_ROUNDS times.

This file knows nothing about Flask or our database: everything it needs is
passed in, which keeps it easy to test.
"""

import json
import logging
import re
from dataclasses import dataclass, field

import requests

from apps.core import tools

logger = logging.getLogger(__name__)

MAX_TURN_CHARS = 2000    # trim very long history messages
TEMPERATURE = 0.2        # low = consistent, factual answers
MAX_OUTPUT_TOKENS = 1024 # upper limit per reply (Ollama calls this "num_predict")
MAX_TOOL_ROUNDS = 5      # safety limit on tool-call round trips per answer


class QwenError(Exception):
    """A problem we can show to the user as-is."""


@dataclass
class QwenReply:
    """The answer plus details we store for monitoring."""
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list = field(default_factory=list)   # [{"name", "args", "result"}]


# ---------- 1. Converting our data into Ollama's format ----------

def tool_definitions():
    """Our tools in Ollama's format (the same format OpenAI uses).

    The model only sees name + description + argument schema. It never sees
    hotel_id: that is not an argument, we add it ourselves in execute().
    """
    return [
        {
            "type": "function",
            "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
        }
        for t in tools.TOOLS.values()
    ]


def build_messages(system_prompt, history, message):
    """Our history -> Ollama's message list.

    Ours and Ollama's use the same roles ("user" / "assistant"), so we only
    clean the data: skip broken rows and trim very long messages.
    """
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role, text = turn.get("role"), turn.get("content")
        if role not in ("user", "assistant") or not isinstance(text, str) or not text.strip():
            continue
        messages.append({"role": role, "content": text[:MAX_TURN_CHARS]})
    messages.append({"role": "user", "content": message})
    return messages


# ---------- 2. One call to Ollama (streamed) ----------

def _chat_stream(url, model, messages, timeout, keep_alive):
    """Send the conversation to Ollama once; yield its reply piece by piece.

    With "stream": true Ollama sends one JSON object per line as the model writes:
        {"message": {"content": "Check-in"}, "done": false}
        {"message": {"content": " is at"}, "done": false}
        ...
        {"message": {"content": ""}, "done": true, "prompt_eval_count": 2998, "eval_count": 49}
    A request to run tools arrives as "message": {"tool_calls": [...]}.
    """
    payload = {
        "model": model,
        "messages": messages,
        "tools": tool_definitions(),
        "stream": True,    # send words as they are written, so the guest sees progress
        "think": False,    # no "thinking" text: 5-10x faster, not needed for our simple tasks
        "keep_alive": keep_alive,  # keep the model in RAM between chats (loading it is slow)
        "options": {"temperature": TEMPERATURE, "num_predict": MAX_OUTPUT_TOKENS},
    }
    try:
        # timeout = max seconds to connect and max seconds of silence between two pieces.
        resp = requests.post(f"{url}/api/chat", json=payload, timeout=timeout, stream=True)
    except requests.ConnectionError as exc:
        logger.error("Cannot reach Ollama at %s", url)
        raise QwenError(
            "The local AI is not running. Start it with: sudo systemctl start ollama"
        ) from exc
    except requests.Timeout as exc:
        logger.warning("Ollama model %s took longer than %s s", model, timeout)
        raise QwenError(
            "The local AI took too long to answer. Please try again or ask a shorter question."
        ) from exc

    if resp.status_code == 404:
        # Ollama answers 404 when the model was never downloaded.
        raise QwenError(f"The local model '{model}' is not installed. Run: ollama pull {model}")
    if resp.status_code != 200:
        logger.error("Ollama error %s: %s", resp.status_code, resp.text[:500])
        raise QwenError("The local AI returned an error. Please try again in a moment.")

    with resp:
        try:
            for line in resp.iter_lines():
                if not line:
                    continue
                chunk = json.loads(line)
                if chunk.get("error"):  # Ollama reports problems mid-stream this way
                    logger.error("Ollama stream error: %s", chunk["error"])
                    raise QwenError("The local AI returned an error. Please try again in a moment.")
                yield chunk
        except requests.RequestException as exc:  # timeout or dropped connection mid-answer
            logger.warning("Ollama stream interrupted: %s", exc)
            raise QwenError("The local AI stopped answering. Please try again.") from exc


# ---------- 3. The tool loop ----------

def stream_answer(*, system_prompt, history, message, hotel_id, url, model, timeout, keep_alive="30m"):
    """Produce Qwen's reply to `message`, yielding events as it goes:

        {"type": "token", "text": "..."}    a few more words of the answer
        {"type": "tool",  "name": "..."}    a tool is being run (e.g. get_room_types)
        {"type": "reset"}                   forget the words shown so far (see below)
        {"type": "final", "reply": QwenReply}   always the last event

    SECURITY: hotel_id comes from our code (the agent's row), never from the model.
    """
    messages = build_messages(system_prompt, history, message)
    tool_log, input_tokens, output_tokens = [], 0, 0

    for _ in range(MAX_TOOL_ROUNDS + 1):
        content, calls = "", []
        for chunk in _chat_stream(url, model, messages, timeout, keep_alive):
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece:
                content += piece
                yield {"type": "token", "text": piece}
            calls += (chunk.get("message") or {}).get("tool_calls") or []
            if chunk.get("done"):
                input_tokens += chunk.get("prompt_eval_count") or 0
                output_tokens += chunk.get("eval_count") or 0

        if not calls:
            break  # Qwen answered with text

        if content:
            # Qwen wrote some words and THEN asked for a tool ("Let me check..."). The real
            # answer comes after the tool, so tell the browser to clear what it showed.
            yield {"type": "reset"}

        # Keep Qwen's tool request in the conversation, then add one result per call.
        messages.append({"role": "assistant", "content": content, "tool_calls": calls})
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            args = fn.get("arguments") or {}
            if isinstance(args, str):  # some models send arguments as a JSON string
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            yield {"type": "tool", "name": name}
            result = tools.execute(name, args, hotel_id=hotel_id)  # hotel_id from US, not the AI
            tool_log.append({"name": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, default=str)})
    else:
        raise QwenError("The assistant needed too many lookups for this question. Please ask something more specific.")

    text = _clean(content)
    if not text:
        raise QwenError("The AI returned an empty reply. Please try rephrasing your question.")

    yield {"type": "final", "reply": QwenReply(
        text=text,
        model=model,
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
        tool_calls=tool_log,
    )}


def answer(**kwargs) -> QwenReply:
    """Same as stream_answer(), but wait for the whole reply and return it."""
    for event in stream_answer(**kwargs):
        if event["type"] == "final":
            return event["reply"]
    raise QwenError("The AI returned an empty reply. Please try rephrasing your question.")


def _clean(text):
    """Remove any leftover <think>...</think> block (safety net; thinking is off)."""
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
