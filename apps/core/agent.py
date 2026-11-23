"""The hotel AI agent.

The platform talks to the agent ONLY through respond(). Everything about how
the answer is produced (prompt, model, tools, later LangGraph) lives here.

Which AI answers is set by LLM_PROVIDER in .env:
  "gemini" = Google's cloud API (the code below)
  "local"  = Qwen on our own machine via Ollama (apps/core/models/qwen.py)

How one answer is produced with Gemini (the "tool loop"):
  1. Send Gemini: system prompt (no hotel data) + conversation + list of tools.
  2. Gemini either answers with text (done), or asks to call tools.
  3. We run the requested tools on hotel_info.db FOR THIS AGENT'S HOTEL ONLY,
     send the results back, and go to step 2. At most MAX_TOOL_ROUNDS times.
"""

import logging
import threading
from dataclasses import dataclass, field
from datetime import date

from flask import current_app
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from apps.core import prompts, tools
from apps.core.models import qwen
from apps.core.services import hotel_service

logger = logging.getLogger(__name__)

MAX_TURN_CHARS = 2000   # trim very long history messages
TEMPERATURE = 0.2       # low = consistent, factual answers
MAX_OUTPUT_TOKENS = 2048
MAX_TOOL_ROUNDS = 5     # safety limit on tool-call round trips per answer


@dataclass
class AgentReply:
    """What respond() returns: the text plus details we store for monitoring."""
    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls: list = field(default_factory=list)   # [{"name", "args", "result"}]


class AgentError(Exception):
    """A problem we can show to the user as-is."""


def greeting_for(agent, hotel):
    """The first message of every conversation."""
    hotel_name = hotel["name"] if hotel else "our hotel"
    return agent["greeting"] or f"Hi! I'm {agent['agent_name']} from {hotel_name}. How can I help you today?"


def system_prompt_for(agent):
    """Build the system prompt. Only the hotel NAME is read here; all facts come via tools."""
    hotel = hotel_service.get_hotel(agent["hotel_id"])
    if hotel is None:
        raise AgentError("This agent's hotel no longer exists in the hotel database.")
    return prompts.build_system_prompt(agent, hotel["name"], date.today())


def _get_client():
    """Create the Gemini client once and reuse it."""
    api_key = current_app.config.get("GEMINI_API_KEY")
    if not api_key:
        raise AgentError("Gemini is not configured. Add GEMINI_API_KEY to the .env file and restart the server.")
    client = current_app.extensions.get("gemini_client")
    if client is None:
        # v1beta: the stable "v1" API rejects system_instruction ("developer instruction").
        client = genai.Client(api_key=api_key, http_options=types.HttpOptions(api_version="v1beta"))
        current_app.extensions["gemini_client"] = client
    return client


def _to_contents(history, message):
    """Convert our chat history into Gemini's format.

    Ours:   [{"role": "user" | "assistant", "content": "..."}]
    Gemini: [Content(role="user" | "model", parts=[Part(text="...")])]
    """
    contents = []
    for turn in history:
        if not isinstance(turn, dict):
            continue
        role = {"user": "user", "assistant": "model"}.get(turn.get("role"))
        text = turn.get("content")
        if role is None or not isinstance(text, str) or not text.strip():
            continue
        if not contents and role == "model":
            continue  # Gemini conversations must start with the user (skips the greeting)
        _append_turn(contents, role, text[:MAX_TURN_CHARS])
    _append_turn(contents, "user", message)
    return contents


def _append_turn(contents, role, text):
    """Add a turn; merge with the previous one if it has the same role.

    Two user turns in a row happen when an AI call failed (the error reply is not
    sent back to the AI). Some models require user/model turns to alternate.
    """
    if contents and contents[-1].role == role:
        contents[-1].parts.append(types.Part(text=text))
    else:
        contents.append(types.Content(role=role, parts=[types.Part(text=text)]))


class ModelUnavailable(AgentError):
    """This model can't answer right now (busy or out of quota). Another model may."""


# Problems with ONE model, so the next model in the queue may work:
#   404 = model not found / retired, 429 = this model's rate limit hit,
#   500/503 = Google's servers are busy or overloaded.
RETRYABLE_CODES = (404, 429, 500, 503)


def _models_to_try():
    """Main model first, then fallbacks from GEMINI_FALLBACK_MODELS (no duplicates)."""
    main = current_app.config["GEMINI_MODEL"]
    fallbacks = current_app.config.get("GEMINI_FALLBACK_MODELS") or []
    return list(dict.fromkeys([main, *fallbacks]))


def _generate(client, agent, model, contents, config):
    """One call to Gemini, with errors turned into AgentError messages."""
    try:
        return client.models.generate_content(model=model, contents=contents, config=config)
    except genai_errors.APIError as exc:
        if exc.code in RETRYABLE_CODES:
            # Expected and temporary: a short warning is enough, no traceback.
            logger.warning("Gemini model %s unavailable (%s) for agent %s", model, exc.code, agent["id"])
            if exc.code == 404:
                raise ModelUnavailable(f"Gemini model '{model}' was not found. Check GEMINI_MODEL / GEMINI_FALLBACK_MODELS in .env.") from exc
            if exc.code == 429:
                raise ModelUnavailable("Too many requests to the AI right now. Please wait a minute and try again.") from exc
            raise ModelUnavailable("The AI service is busy right now. Please try again in a moment.") from exc
        logger.exception("Gemini API error for agent %s (model %s)", agent["id"], model)
        if exc.code in (401, 403):
            raise AgentError("Gemini rejected the API key. Check GEMINI_API_KEY in .env.") from exc
        raise AgentError("The AI service returned an error. Please try again in a moment.") from exc
    except Exception as exc:  # network problems, timeouts, ...
        logger.exception("Could not reach Gemini for agent %s", agent["id"])
        raise AgentError("Could not reach the AI service. Check your internet connection.") from exc


def respond(agent, message: str, history: list) -> AgentReply:
    """Return the agent's reply to `message`.

    agent:   the agent row from HART_Admin_Agent.db (has hotel_id, agent_name, admin_prompt...).
    message: the latest user message.
    history: earlier turns [{"role": "user" | "assistant", "content": "..."}] (from the DB).
    """
    provider = current_app.config.get("LLM_PROVIDER", "gemini")
    if provider == "local":
        return _respond_local(agent, message, history)
    if provider != "gemini":
        raise AgentError(f"Unknown LLM_PROVIDER '{provider}' in .env. Use 'gemini' or 'local'.")

    client = _get_client()
    config = types.GenerateContentConfig(
        system_instruction=system_prompt_for(agent),
        tools=tools.declarations(),
        # We run the tools ourselves, so we control hotel_id and can log every call.
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        temperature=TEMPERATURE,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )
    # Try the main model; if it's busy or out of quota, redo the whole answer on a fallback.
    # Safe to redo because every tool is read-only.
    last_error = None
    for model in _models_to_try():
        try:
            return _answer_with(client, agent, model, config, _to_contents(history, message))
        except ModelUnavailable as exc:
            last_error = exc
    raise last_error


def warm_up(agent):
    """Local model only: pre-read this agent's system prompt + tools in the background,
    so the guest's first question starts faster. Returns immediately."""
    cfg = current_app.config
    if cfg.get("LLM_PROVIDER") != "local":
        return
    try:
        system_prompt = system_prompt_for(agent)   # needs the database, so build it now
    except AgentError:
        return
    threading.Thread(
        target=qwen.warm_up, daemon=True,
        kwargs=dict(system_prompt=system_prompt, url=cfg["OLLAMA_URL"], model=cfg["OLLAMA_MODEL"],
                    keep_alive=cfg.get("OLLAMA_KEEP_ALIVE", "30m"),
                    num_ctx=cfg.get("OLLAMA_NUM_CTX", qwen.DEFAULT_NUM_CTX),
                    timeout=cfg["OLLAMA_TIMEOUT"]),
    ).start()


def respond_stream(agent, message: str, history: list):
    """Like respond(), but yields events while the answer is being written:

        {"type": "token", "text": "..."}   more words of the answer
        {"type": "tool",  "name": "..."}   a database tool is being run
        {"type": "reset"}                  clear the words shown so far
        {"type": "final", "reply": AgentReply}   always the last event

    Raises AgentError (possibly after some events) when the answer fails.
    """
    if current_app.config.get("LLM_PROVIDER", "gemini") == "local":
        yield from _stream_local(agent, message, history)
        return
    # Gemini isn't streamed yet: send the whole answer as one piece.
    reply = respond(agent, message, history)
    yield {"type": "token", "text": reply.text}
    yield {"type": "final", "reply": reply}


def _respond_local(agent, message, history):
    """Answer with the local Qwen model and wait for the whole reply."""
    for event in _stream_local(agent, message, history):
        if event["type"] == "final":
            return event["reply"]
    raise AgentError("The AI returned an empty reply. Please try rephrasing your question.")


def _stream_local(agent, message, history):
    """Answer with the local Qwen model (data never leaves our server), streamed."""
    cfg = current_app.config
    max_history = cfg.get("OLLAMA_MAX_HISTORY", 6)
    events = qwen.stream_answer(
        system_prompt=system_prompt_for(agent),
        # Only the last few messages: less to read (faster on CPU) and less old text to copy.
        history=history[-max_history:] if max_history > 0 else [],
        message=message,
        hotel_id=agent["hotel_id"],   # from the agent's row, never from the AI
        url=cfg["OLLAMA_URL"],
        model=cfg["OLLAMA_MODEL"],
        timeout=cfg["OLLAMA_TIMEOUT"],
        keep_alive=cfg.get("OLLAMA_KEEP_ALIVE", "30m"),
        num_ctx=cfg.get("OLLAMA_NUM_CTX", qwen.DEFAULT_NUM_CTX),
    )
    try:
        for event in events:
            if event["type"] == "final":
                r = event["reply"]   # Qwen's reply -> our app's AgentReply
                event = {"type": "final", "reply": AgentReply(
                    text=r.text, model=r.model, input_tokens=r.input_tokens,
                    output_tokens=r.output_tokens, tool_calls=r.tool_calls,
                )}
            yield event
    except qwen.QwenError as exc:
        raise AgentError(str(exc)) from exc   # the route already shows AgentError messages


def _answer_with(client, agent, model, config, contents):
    """The tool loop for one model."""
    tool_log, input_tokens, output_tokens = [], 0, 0

    for _ in range(MAX_TOOL_ROUNDS + 1):
        response = _generate(client, agent, model, contents, config)
        usage = response.usage_metadata
        input_tokens += getattr(usage, "prompt_token_count", None) or 0
        output_tokens += getattr(usage, "candidates_token_count", None) or 0

        calls = response.function_calls or []
        if not calls:
            break  # Gemini answered with text

        # Keep Gemini's tool request in the conversation, then add our results.
        contents.append(response.candidates[0].content)
        result_parts = []
        for call in calls:
            args = dict(call.args or {})
            result = tools.execute(call.name, args, hotel_id=agent["hotel_id"])  # hotel_id from US, not the AI
            tool_log.append({"name": call.name, "args": args, "result": result})
            result_parts.append(types.Part(function_response=types.FunctionResponse(
                id=call.id, name=call.name, response=result,
            )))
        contents.append(types.Content(role="user", parts=result_parts))
    else:
        raise AgentError("The assistant needed too many lookups for this question. Please ask something more specific.")

    text = (response.text or "").strip()
    if not text:
        raise AgentError("The AI returned an empty reply. Please try rephrasing your question.")

    return AgentReply(
        text=text,
        model=model,  # the model that actually answered (may be a fallback)
        input_tokens=input_tokens or None,
        output_tokens=output_tokens or None,
        tool_calls=tool_log,
    )
