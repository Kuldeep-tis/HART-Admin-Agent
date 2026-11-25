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

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field

import requests

from apps.core import tools

logger = logging.getLogger(__name__)

MAX_TURN_CHARS = 500     # trim old messages: on CPU every history token is re-read on each answer (~30 tokens/s)
TEMPERATURE = 0.2        # low = consistent, factual answers
MAX_OUTPUT_TOKENS = 1024 # upper limit per reply (Ollama calls this "num_predict")
MAX_TOOL_ROUNDS = 5      # safety limit on tool-call round trips per answer
DEFAULT_NUM_CTX = 4096   # the model's working memory in tokens (see OLLAMA_NUM_CTX)
LIST_ROW = "\n• #"       # a row of a Python-written booking list (booking_tools._render_bookings)

# Guard against made-up facts: every number staff ask about (times, prices, counts, revenue)
# lives in the database. So a reply with a number but no tool call is retried once with this hint.
HAS_NUMBER = re.compile(r"\d")
LOOKUP_HINT = ("\n\n(Look this up with the right tool now and answer only from its result. "
               "If no tool has it, say you don't have that information.)")
NO_LOOKUP_REPLY = ("I couldn't look that up. Please ask the full question, "
                   "for example \"What was the revenue last month?\"")


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
        if role == "assistant" and LIST_ROW in text:
            # A list Python wrote earlier: keep only its title. Otherwise the model answers
            # follow-ups ("only the checked-in ones") by filtering old rows instead of the database.
            text = text.split("\n", 1)[0] + " (shown as a table; look it up again for details)"
        messages.append({"role": role, "content": text if len(text) <= MAX_TURN_CHARS
                         else text[:MAX_TURN_CHARS] + "…"})
    messages.append({"role": "user", "content": message})
    return messages


def _tool_message(result):
    """A tool result as compact JSON: no spaces, ₹ kept as one character. Fewer tokens to read."""
    return json.dumps(result, default=str, separators=(",", ":"), ensure_ascii=False)


# ---------- 2. One call to Ollama (streamed) ----------

def _payload(model, messages, keep_alive, num_ctx, stream=True, max_tokens=MAX_OUTPUT_TOKENS):
    """The request body for Ollama's /api/chat.

    Warm-up and real chats must send the same tools and the same num_ctx: a different
    num_ctx makes Ollama reload the model, and different tools break the cache.
    """
    return {
        "model": model,
        "messages": messages,
        "tools": tool_definitions(),
        "stream": stream,  # send words as they are written, so the guest sees progress
        "think": False,    # no "thinking" text: 5-10x faster, not needed for our simple tasks
        "keep_alive": keep_alive,  # keep the model in RAM between chats (loading it is slow)
        "options": {"temperature": TEMPERATURE, "num_predict": max_tokens, "num_ctx": num_ctx},
    }


WARM_UP_EVERY_SECONDS = 600     # don't warm the same prompt again within 10 minutes
_warm_lock = threading.Lock()   # only one warm-up at a time: on CPU Ollama answers one request at a time
_warmed_at = {}                 # (url, model, prompt hash) -> time of the last warm-up


def warm_up(*, system_prompt, url, model, keep_alive="30m", num_ctx=DEFAULT_NUM_CTX, timeout=180):
    """Make Ollama read the system prompt and tools now, before the guest asks anything.

    Ollama remembers (caches) the start of recent inputs. Every chat starts with the
    same system prompt + tools (~1,000+ tokens), so after a warm-up the first real
    question skips re-reading them (measured: 0.8 s instead of ~5 s on CPU).

    Skipped when another warm-up is running or this prompt was warmed recently, so
    refreshing the page doesn't queue up warm-ups in front of real questions.
    Best effort: any problem is only logged.
    """
    key = (url, model, hashlib.sha256(system_prompt.encode()).hexdigest())
    if time.monotonic() - _warmed_at.get(key, -WARM_UP_EVERY_SECONDS) < WARM_UP_EVERY_SECONDS:
        return
    if not _warm_lock.acquire(blocking=False):
        return
    try:
        _warmed_at[key] = time.monotonic()
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": "hi"}]
        requests.post(f"{url}/api/chat", timeout=timeout,
                      json=_payload(model, messages, keep_alive, num_ctx, stream=False, max_tokens=1))
    except requests.RequestException as exc:
        logger.info("Ollama warm-up skipped: %s", exc)
    finally:
        _warm_lock.release()


def _chat_stream(url, model, messages, timeout, keep_alive, num_ctx=DEFAULT_NUM_CTX):
    """Send the conversation to Ollama once; yield its reply piece by piece.

    With "stream": true Ollama sends one JSON object per line as the model writes:
        {"message": {"content": "Check-in"}, "done": false}
        {"message": {"content": " is at"}, "done": false}
        ...
        {"message": {"content": ""}, "done": true, "prompt_eval_count": 2998, "eval_count": 49}
    A request to run tools arrives as "message": {"tool_calls": [...]}.
    """
    payload = _payload(model, messages, keep_alive, num_ctx)
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

def stream_answer(*, system_prompt, history, message, hotel_id, url, model, timeout, keep_alive="30m",
                  num_ctx=DEFAULT_NUM_CTX):
    """Produce Qwen's reply to `message`, yielding events as it goes:

        {"type": "token", "text": "..."}    a few more words of the answer
        {"type": "table", "table": {...}}   the whole answer as a table (written by Python, see tools.table)
        {"type": "tool",  "name": "..."}    a tool is being run (e.g. get_room_types)
        {"type": "reset"}                   forget the words shown so far (see below)
        {"type": "final", "reply": QwenReply}   always the last event

    SECURITY: hotel_id comes from our code (the agent's row), never from the model.
    """
    messages = build_messages(system_prompt, history, message)
    question_at = len(messages) - 1   # the staff member's question (tool messages get added after it)
    tool_log, input_tokens, output_tokens = [], 0, 0
    guard_used = False   # the "numbers without a lookup" retry happens at most once per answer

    for _ in range(MAX_TOOL_ROUNDS + 1):
        content, calls, unchecked = "", [], False
        # Numbers are only trusted after a lookup that worked (a failed lookup gives no facts).
        looked_up = any("error" not in c["result"] for c in tool_log)
        stream = _chat_stream(url, model, messages, timeout, keep_alive, num_ctx)
        for chunk in stream:
            piece = (chunk.get("message") or {}).get("content") or ""
            if piece:
                if not looked_up and HAS_NUMBER.search(content + piece):
                    unchecked = True   # a number, and nothing was looked up: probably copied or invented
                    break
                content += piece
                yield {"type": "token", "text": piece}
            calls += (chunk.get("message") or {}).get("tool_calls") or []
            if chunk.get("done"):
                input_tokens += chunk.get("prompt_eval_count") or 0
                output_tokens += chunk.get("eval_count") or 0
                if (chunk.get("prompt_eval_count") or 0) >= num_ctx * 0.9:
                    logger.warning("Input (%s tokens) is close to num_ctx=%s: the start may be cut off. "
                                   "Lower OLLAMA_MAX_HISTORY or raise OLLAMA_NUM_CTX.",
                                   chunk.get("prompt_eval_count"), num_ctx)

        if unchecked:
            # Follow-ups like "and last month?" made the model copy the previous answer's style and
            # make up numbers without calling a tool. Stop it at the first digit (closing the stream
            # tells Ollama to stop writing), clear the screen, and ask again with a nudge.
            stream.close()
            yield {"type": "reset"}
            if guard_used:   # asked twice, still no lookup: say so rather than show a made-up number
                logger.warning("Answer still had numbers without a lookup; refusing to guess")
                content = NO_LOOKUP_REPLY
                yield {"type": "token", "text": content}
                break
            guard_used = True
            logger.info("Answer had numbers without a lookup; asking again with a tool hint")
            # Start again from the question (without failed lookups), plus the hint.
            del messages[question_at:]
            messages.append({"role": "user", "content": message + LOOKUP_HINT})
            continue

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
            if len(calls) == 1:   # several calls = a deliberate comparison ("this vs last month"): leave them
                args = tools.steer_period(name, args, message)   # the period the staff member named, not a guess
            yield {"type": "tool", "name": name}
            result = tools.execute(name, args, hotel_id=hotel_id)  # hotel_id from US, not the AI
            tool_log.append({"name": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_name": name, "content": _tool_message(result)})

        # Shortcut: one list-type tool, so Python writes the answer. This skips a whole second
        # model call (re-reading everything + typing every row), and the numbers are always exact.
        rendered = tools.render(name, result) if len(calls) == 1 else None
        if rendered:
            content = rendered   # the text version is what we store and use as chat history
            table = tools.table(name, result)
            yield {"type": "table", "table": table} if table else {"type": "token", "text": rendered}
            break
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
