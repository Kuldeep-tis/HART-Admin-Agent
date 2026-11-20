// Preview chat widget: open/close, send messages, show replies.
(() => {
  const root = document.querySelector("[data-chat]");
  if (!root) return;

  const url = root.dataset.chatUrl;
  const initials = root.dataset.initials || "";
  const panel = root.querySelector(".chat-panel");
  const launcher = root.querySelector("[data-chat-launcher]");
  const log = root.querySelector("[data-chat-log]");
  const form = root.querySelector("[data-chat-form]");
  const input = root.querySelector("[data-chat-text]");
  const sendBtn = root.querySelector("[data-chat-send]");
  const nudge = root.querySelector("[data-chat-nudge]");
  const historyUrl = root.dataset.historyUrl;

  // Ids kept in this browser. localStorage can be blocked (private mode), so never let it crash.
  const store = {
    get: (k) => { try { return localStorage.getItem(k); } catch { return null; } },
    set: (k, v) => { try { localStorage.setItem(k, v); } catch { /* ignore */ } },
    del: (k) => { try { localStorage.removeItem(k); } catch { /* ignore */ } },
  };
  const GUEST_KEY = "hart_guest_id";                              // one per browser = the "guest"
  const SESSION_KEY = `hart_session_agent_${root.dataset.agentId}`; // current conversation with this agent

  const timeNow = () => new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  // Server times are UTC text like "2026-11-19 10:42:05".
  const timeAt = (utc) => new Date(`${utc.replace(" ", "T")}Z`).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const scrollDown = () => (log.scrollTop = log.scrollHeight);

  // Stamp the server-rendered greeting, then remember the starting state for "New conversation".
  const stamp = (msg) => {
    const time = document.createElement("time");
    time.textContent = timeNow();
    msg.querySelector(".msg-body").append(time);
  };
  stamp(log.querySelector("[data-greeting]"));
  const initialLog = log.innerHTML;
  let busy = false;

  function setOpen(open) {
    panel.hidden = !open;
    root.classList.toggle("is-open", open);
    launcher.setAttribute("aria-expanded", String(open));
    if (open) {
      nudge?.classList.add("dismissed");
      scrollDown();
      input.focus();
    } else {
      launcher.focus();
    }
  }

  function addMessage(kind, text, createdAt, toolsUsed = []) {
    const sender = kind === "user" ? "user" : "bot";
    const prev = log.lastElementChild;
    const msg = document.createElement("div");
    msg.className = `msg msg-${kind}`;
    msg.dataset.sender = sender;

    // Group with the previous message if it came from the same side.
    if (prev?.classList.contains("msg") && prev.dataset.sender === sender) {
      prev.classList.add("is-stacked");
      msg.classList.add("follows-same");
    }

    if (sender === "bot") {
      const av = document.createElement("span");
      av.className = "msg-avatar";
      av.textContent = initials;
      msg.append(av);
    }
    const body = document.createElement("div");
    body.className = "msg-body";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.textContent = text; // textContent, never innerHTML: user text can't inject HTML
    const time = document.createElement("time");
    time.textContent = createdAt ? timeAt(createdAt) : timeNow();
    body.append(bubble);
    if (toolsUsed.length) {
      // Shows which live database lookups the agent made for this answer.
      const used = document.createElement("div");
      used.className = "msg-tools";
      used.textContent = toolsUsed.join(" · ");
      body.append(used);
    }
    body.append(time);
    msg.append(body);

    // Show a time once per minute: hide it on the previous message if it says the same.
    // (Skipped for the empty typing indicator, which never keeps its time.)
    const prevMsg = [...log.querySelectorAll(".msg")].pop();
    if (text && prevMsg?.querySelector("time")?.textContent === time.textContent) {
      prevMsg.classList.add("same-time");
    }
    log.append(msg);
    scrollDown();
    return msg;
  }

  function showTyping() {
    const msg = addMessage("bot", "");
    msg.querySelector("time").remove();
    const bubble = msg.querySelector(".bubble");
    bubble.classList.add("typing");
    bubble.setAttribute("aria-label", "Agent is typing");
    bubble.innerHTML = "<i></i><i></i><i></i>";
    return msg;
  }

  function autosize() {
    input.style.height = "auto";
    const height = Math.min(input.scrollHeight, 120);
    input.style.height = `${height}px`;
    input.style.overflowY = input.scrollHeight > 120 ? "auto" : "hidden";
    sendBtn.disabled = busy || !input.value.trim();
  }

  async function send(text) {
    busy = true;
    log.querySelector("[data-chat-suggestions]")?.remove();
    addMessage("user", text);
    input.value = "";
    autosize();
    const typing = showTyping();

    try {
      const minDelay = new Promise((r) => setTimeout(r, 600)); // feels more natural than an instant reply
      const res = await fetch(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRF-Token": document.querySelector('meta[name="csrf-token"]')?.content || "",
        },
        // No history here: the server reads it from the database using session_id.
        // stream: true = send the answer word by word instead of all at the end.
        body: JSON.stringify({
          message: text, session_id: store.get(SESSION_KEY), guest_id: store.get(GUEST_KEY), stream: true,
        }),
      });
      await minDelay;

      const type = res.headers.get("content-type") || "";
      if (type.includes("application/x-ndjson")) {
        await readStream(res, typing);
        return;
      }
      // Not streamed: validation errors (400/429) come back as one JSON object.
      // If the session expired, Flask redirects to the login page (HTML, not JSON).
      if (!type.includes("application/json")) {
        throw new Error("Your session has expired. Please log in again.");
      }
      const data = await res.json();
      if (data.session_id) store.set(SESSION_KEY, data.session_id);
      if (data.guest_id) store.set(GUEST_KEY, data.guest_id);
      if (!res.ok) throw new Error(data.error || "Something went wrong. Please try again.");

      typing.remove();
      addMessage("bot", data.reply, null, data.tools || []);
    } catch (err) {
      typing.remove();
      addMessage("error", err.message || "Could not reach the server.");
    } finally {
      busy = false;
      autosize();
      input.focus();
    }
  }

  // Read the streamed answer: one JSON object per line (NDJSON), shown as it arrives.
  async function readStream(res, typing) {
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let msg = null;   // the bot bubble, created when the first words arrive
    let text = "";
    let finished = false;

    const bubble = () => {
      if (!msg) {
        typing.remove();
        msg = addMessage("bot", "");
      }
      return msg.querySelector(".bubble");
    };
    const showTools = (target, labels) => {
      // Small line under the bubble, e.g. "Checked room types".
      const body = target.querySelector(".msg-body");
      let el = body.querySelector(".msg-tools");
      if (!el) {
        el = document.createElement("div");
        el.className = "msg-tools";
        body.insertBefore(el, body.querySelector("time"));
      }
      el.textContent = labels.join(" · ");
    };

    const handle = (event) => {
      if (event.session_id) store.set(SESSION_KEY, event.session_id);
      if (event.guest_id) store.set(GUEST_KEY, event.guest_id);
      if (event.type === "token") {
        text += event.text;
        bubble().textContent = text; // textContent, never innerHTML
        scrollDown();
      } else if (event.type === "reset") {
        text = "";
        if (msg) msg.querySelector(".bubble").textContent = "";
      } else if (event.type === "tool") {
        showTools(msg || typing, [event.label]); // "the agent is looking something up"
        scrollDown();
      } else if (event.type === "done") {
        finished = true;
        bubble();
        if (event.tools?.length) showTools(msg, event.tools);
      } else if (event.type === "error") {
        finished = true;
        throw new Error(event.error || "Something went wrong. Please try again.");
      }
    };

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let newline;
      while ((newline = buffer.indexOf("\n")) >= 0) {
        const lineText = buffer.slice(0, newline).trim();
        buffer = buffer.slice(newline + 1);
        if (lineText) handle(JSON.parse(lineText));
      }
    }
    if (!finished) throw new Error("The reply was cut off. Please try again.");
  }

  function reset() {
    if (busy) return;
    log.innerHTML = initialLog;
    store.del(SESSION_KEY); // next message starts a new conversation (same guest)
    input.value = "";
    autosize();
    input.focus();
  }

  launcher.addEventListener("click", () => setOpen(panel.hidden));
  root.querySelector("[data-chat-close]").addEventListener("click", () => setOpen(false));
  root.querySelector("[data-chat-reset]").addEventListener("click", reset);
  nudge?.addEventListener("click", () => setOpen(true));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) setOpen(false);
  });

  // Suggested questions (delegated, so it still works after a reset).
  log.addEventListener("click", (e) => {
    const chip = e.target.closest("[data-chat-suggestions] button");
    if (chip && !busy) send(chip.textContent.trim());
  });

  input.addEventListener("input", autosize);
  input.addEventListener("keydown", (e) => {
    // Enter sends, Shift+Enter adds a new line.
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (text && !busy) send(text);
  });

  // After a page refresh: reload this session's messages from the server.
  async function restore() {
    const sessionId = store.get(SESSION_KEY);
    if (!sessionId || !historyUrl) return;
    try {
      const query = new URLSearchParams({ session_id: sessionId, guest_id: store.get(GUEST_KEY) || "" });
      const res = await fetch(`${historyUrl}?${query}`, { headers: { Accept: "application/json" } });
      if (res.status === 404) return store.del(SESSION_KEY); // deleted or not ours: start fresh
      if (!res.ok || !(res.headers.get("content-type") || "").includes("application/json")) return;
      const { messages } = await res.json();
      if (!messages.some((m) => m.role === "user")) return;

      log.querySelector("[data-chat-suggestions]")?.remove();
      const [greetingMsg, ...rest] = messages; // the first stored message is the greeting
      log.querySelector("[data-greeting] .bubble").textContent = greetingMsg.content;
      log.querySelector("[data-greeting] time").textContent = timeAt(greetingMsg.created_at);
      rest.forEach((m) => {
        const kind = m.role === "user" ? "user" : m.status === "error" ? "error" : "bot";
        addMessage(kind, m.content, m.created_at, m.tools || []);
      });
    } catch {
      /* offline or server down: just show a fresh chat */
    }
  }
  restore();
})();
