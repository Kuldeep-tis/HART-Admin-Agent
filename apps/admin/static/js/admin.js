// Submit the surrounding form as soon as a [data-autosubmit] control changes
// (used by the agent on/off switches).
document.querySelectorAll("[data-autosubmit]").forEach((el) => {
  el.addEventListener("change", () => el.form.submit());
});

// Toasts: auto-hide after TOAST_MS, pause while hovered, close on ×.
const TOAST_MS = 4000;

function hideToast(toast) {
  if (toast.classList.contains("hiding")) return;
  toast.classList.add("hiding");
  toast.addEventListener("animationend", () => toast.remove(), { once: true });
}

document.querySelectorAll("[data-toast]").forEach((toast) => {
  toast.style.setProperty("--toast-duration", `${TOAST_MS}ms`);
  let remaining = TOAST_MS;
  let startedAt = Date.now();
  let timer = setTimeout(() => hideToast(toast), remaining);

  toast.addEventListener("mouseenter", () => {
    clearTimeout(timer);
    remaining -= Date.now() - startedAt;
  });
  toast.addEventListener("mouseleave", () => {
    startedAt = Date.now();
    timer = setTimeout(() => hideToast(toast), remaining);
  });
  toast.querySelector("[data-toast-close]").addEventListener("click", () => hideToast(toast));
});

// Settings forms: keep Save/Reset disabled until something actually changes.
document.querySelectorAll("[data-dirty-form]").forEach((form) => {
  const buttons = form.querySelectorAll("[data-dirty-enable]");
  const snapshot = () => JSON.stringify([...new FormData(form)]);
  const initial = snapshot();
  const update = () => {
    const dirty = snapshot() !== initial;
    buttons.forEach((b) => (b.disabled = !dirty));
  };
  form.addEventListener("input", update);
  form.addEventListener("reset", () => setTimeout(update)); // values reset after the event
  update();
});

// Tabs: click or arrow keys to switch; the URL hash (#agent, #hotel) remembers the tab.
document.querySelectorAll("[data-tabs]").forEach((root) => {
  const tabs = [...root.querySelectorAll('[role="tab"]')];

  const show = (name, updateUrl) => {
    tabs.forEach((tab) => {
      const active = tab.dataset.tab === name;
      tab.setAttribute("aria-selected", active);
      tab.tabIndex = active ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !active;
    });
    if (updateUrl) history.replaceState(null, "", `#${name}`);
  };

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => show(tab.dataset.tab, true));
    tab.addEventListener("keydown", (e) => {
      const step = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!step) return;
      const next = tabs[(i + step + tabs.length) % tabs.length];
      show(next.dataset.tab, true);
      next.focus();
    });
  });

  const fromHash = location.hash.slice(1);
  show(tabs.some((t) => t.dataset.tab === fromHash) ? fromHash : tabs[0].dataset.tab, false);
});

// Scrollable transcripts: open at the latest message; show "Latest message" after scrolling up.
document.querySelectorAll("[data-autoscroll]").forEach((box) => {
  const jump = box.parentElement.querySelector("[data-jump-latest]");
  const toBottom = (smooth) => box.scrollTo({ top: box.scrollHeight, behavior: smooth ? "smooth" : "auto" });

  toBottom(false);
  if (!jump) return;
  box.addEventListener("scroll", () => {
    jump.hidden = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
  }, { passive: true });
  jump.addEventListener("click", () => toBottom(true));
});
