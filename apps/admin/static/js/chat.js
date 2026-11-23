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

  // Draw a table answer ({title, columns, rows}) inside a bot bubble.
  // Built with createElement + textContent only, so no data can inject HTML.
  function renderTable(bubble, table) {
    bubble.textContent = "";
    bubble.classList.add("has-table");
    const title = document.createElement("p");
    title.className = "chat-table-title";
    title.textContent = table.title;
    const wrap = document.createElement("div");
    wrap.className = "chat-table-wrap"; // scrolls sideways on narrow screens
    const el = document.createElement("table");
    el.className = "chat-table";
    const headRow = el.createTHead().insertRow();
    table.columns.forEach((name) => {
      const th = document.createElement("th");
      th.scope = "col";
      th.textContent = name;
      headRow.append(th);
    });
    const body = el.createTBody();
    table.rows.forEach((cells) => {
      const row = body.insertRow();
      cells.forEach((value, i) => {
        const cell = row.insertCell();
        const col = table.columns[i];
        cell.dataset.col = col; // lets CSS style columns (e.g. right-align amounts)
        if (col === "Status") {
          const badge = document.createElement("span");
          badge.className = "chat-status";
          badge.dataset.status = value; // colour per status: cancelled, confirmed, ...
          badge.textContent = value;
          cell.append(badge);
        } else {
          // "Sea View\n#40": first line normal, the rest smaller and grey.
          const [main, ...sub] = String(value).split("\n");
          cell.textContent = main;
          sub.forEach((line) => {
            const small = document.createElement("span");
            small.className = "cell-sub";
            small.textContent = line;
            cell.append(small);
          });
        }
      });
    });
    wrap.append(el);
    bubble.append(title, wrap);
  }

  // "Agent is working" bubble: dots + what it is doing ("Finding bookings…") + seconds waited.
  // A local model needs a few seconds; showing progress makes the wait feel shorter.
  const SHOW_SECONDS_AFTER = 3;
  function showTyping() {
    const msg = addMessage("bot", "");
    msg.querySelector("time").remove();
    const bubble = msg.querySelector(".bubble");
    bubble.classList.add("typing");
    bubble.setAttribute("aria-label", "Agent is working");
    bubble.innerHTML = '<span class="typing-dots"><i></i><i></i><i></i></span>';
    const status = document.createElement("span");
    status.className = "typing-status";
    status.textContent = "Thinking…";
    const clock = document.createElement("span");
    clock.className = "typing-clock";
    bubble.append(status, clock);

    const started = Date.now();
    const timer = setInterval(() => {
      if (!msg.isConnected) return clearInterval(timer); // reply arrived: the bubble was removed
      const seconds = Math.floor((Date.now() - started) / 1000);
      if (seconds >= SHOW_SECONDS_AFTER) clock.textContent = `${seconds}s`;
    }, 500);
    msg.setStatus = (text) => { status.textContent = text; };
    return msg;
  }

  function autosize() {
    input.style.height = "auto";
    const height = Math.min(input.scrollHeight, 120);
    input.style.height = `${height}px`;
    input.style.overflowY = input.scrollHeight > 120 ? "auto" : "hidden";
    sendBtn.disabled = busy || !input.value.trim();
  }

  // ---------- Voice input (mic button) ----------
  // Uses the browser's speech recognition and puts the words in the input box, so the user
  // can check them before sending. NOTE: Chrome sends the recorded audio to Google's servers
  // to recognise it (hotel data still stays local, but the spoken question does not).
  // To keep audio local too, replace this with a local engine such as Whisper.
  const VOICE_LANG = "en-IN";   // Indian English: better with Indian accents, names and ₹ amounts
  const micBtn = root.querySelector("[data-chat-mic]");
  const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
  const placeholder = input.placeholder;
  let recognition = null;   // the running recording, if any
  let sendWhenDone = false; // push-to-talk released: send the question once the last words are in

  // Mic problems are shown for a few seconds under the input box, not as chat messages.
  const foot = root.querySelector(".chat-foot");
  const footText = foot?.textContent;
  let noteTimer = null;
  function showVoiceNote(text) {
    if (!foot) return;
    foot.textContent = text;
    foot.classList.add("is-error");
    clearTimeout(noteTimer);
    noteTimer = setTimeout(() => {
      foot.textContent = footText;
      foot.classList.remove("is-error");
    }, 6000);
  }

  function stopVoice() {
    recognition?.stop();
  }

  const MIC_HINT = "Speak your question (or hold Alt+M to talk and send)";

  function setListening(on, held = false) {
    micBtn.classList.toggle("is-listening", on);
    micBtn.setAttribute("aria-pressed", String(on));
    micBtn.setAttribute("aria-label", on ? "Stop voice input" : MIC_HINT);
    micBtn.title = on ? "Stop listening" : MIC_HINT;
    input.placeholder = !on ? placeholder : held ? "Listening… release Alt+M to send" : "Listening… speak now";
  }

  // held = push-to-talk (Alt+M is held down): keep listening through pauses until the keys are released.
  function startVoice(held = false) {
    recognition = new Recognition();
    recognition.lang = VOICE_LANG;
    recognition.interimResults = true;   // show the words while the user is still speaking
    recognition.continuous = held;       // click: stop after a short pause; hold: stop on release
    const before = input.value.trim() ? `${input.value.trim()} ` : ""; // keep what was typed

    recognition.onresult = (e) => {
      input.value = before + [...e.results].map((r) => r[0].transcript).join("");
      autosize();
    };
    recognition.onerror = (e) => {
      const messages = {
        "not-allowed": "Microphone access is blocked. Allow it with the icon in the address bar, then try again.",
        "service-not-allowed": "Voice input is not allowed in this browser.",
        "audio-capture": "No microphone was found.",
        network: "Voice input needs an internet connection.",
      };
      sendWhenDone = false; // something went wrong: never send half a question
      if (messages[e.error]) showVoiceNote(messages[e.error]); // "no-speech"/"aborted": just stop
    };
    // Chrome delivers the last words AFTER stop(), so push-to-talk sends here, not on key release.
    recognition.onend = () => {
      recognition = null;
      setListening(false);
      autosize();
      input.focus();
      const text = input.value.trim();
      if (sendWhenDone && text && !busy) send(text);
      sendWhenDone = false;
    };
    setListening(true, held);
    recognition.start();
  }

  if (micBtn && Recognition) {
    micBtn.hidden = false; // only shown where the browser supports speech recognition
    micBtn.title = MIC_HINT;
    micBtn.setAttribute("aria-label", MIC_HINT);
    micBtn.addEventListener("click", () => (recognition ? stopVoice() : startVoice()));

    // Push-to-talk: hold Alt+M while the chat is open, release to send the question.
    let holding = false;
    document.addEventListener("keydown", (e) => {
      if (!(e.altKey && e.code === "KeyM") || panel.hidden) return;
      e.preventDefault();               // don't type "µ" or trigger browser shortcuts
      if (e.repeat || holding || recognition) return;   // holding a key repeats keydown: start once
      holding = true;
      input.focus();
      startVoice(true);
    });
    const release = (e) => {
      if (holding && (e.type === "blur" || e.code === "KeyM" || e.key === "Alt")) {
        holding = false;
        sendWhenDone = e.type !== "blur"; // keys released: send; switched window by accident: just stop
        stopVoice();
      }
    };
    document.addEventListener("keyup", release);
    window.addEventListener("blur", release); // switched window while holding: stop listening
  }

  async function send(text) {
    stopVoice(); // sending while listening: stop, so late words don't refill the box
    busy = true;
    log.querySelector("[data-chat-suggestions]")?.remove();
    addMessage("user", text);
    input.value = "";
    autosize();
    const typing = showTyping();

    try {
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
      } else if (event.type === "table") {
        renderTable(bubble(), event.table);
        scrollDown();
      } else if (event.type === "reset") {
        text = "";
        if (msg) msg.querySelector(".bubble").textContent = "";
      } else if (event.type === "tool") {
        if (!msg) typing.setStatus(event.progress || "Looking up hotel data…"); // e.g. "Finding bookings…"
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
        const msg = addMessage(kind, m.content, m.created_at, m.tools || []);
        if (m.table) renderTable(msg.querySelector(".bubble"), m.table);
      });
    } catch {
      /* offline or server down: just show a fresh chat */
    }
  }
  restore();
})();
