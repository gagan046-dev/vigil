/* Toasts, confirm dialog, error banners, empty states, skeletons, <time> elements. */
import { el, icon, mount } from "../dom.js";
import { exact, relative } from "../format.js";

// ------------------------------------------------------------------ toasts

export function toast(message, opts = {}) {
  const host = document.getElementById("toasts");
  const close = el("button", { type: "button", class: "icon-btn", "aria-label": "Dismiss" }, icon("x"));
  const node = el("div", { class: "toast" + (opts.error ? " toast--error" : "") },
    el("div", { class: "toast__msg" }, message,
      opts.link ? [" ", el("a", { href: opts.link.href, text: opts.link.label })] : null),
    close);
  const remove = () => node.remove();
  close.addEventListener("click", remove);
  host.append(node);
  setTimeout(remove, opts.error ? 8000 : 4500);
}

// ------------------------------------------------------------------ confirm

export function confirmDialog({ title, text, confirmLabel = "Confirm", danger = false }) {
  return new Promise((resolve) => {
    const cancel = el("button", { type: "button", class: "btn", text: "Cancel" });
    const ok = el("button", { type: "button", class: "btn " + (danger ? "btn--danger" : "btn--primary"), text: confirmLabel });
    const titleId = "dlg-" + Math.random().toString(36).slice(2);
    const dlg = el("dialog", { class: "modal", "aria-labelledby": titleId },
      el("div", { class: "modal__body" },
        el("h2", { class: "modal__title", id: titleId, text: title }),
        el("p", { class: "modal__text", text })),
      el("div", { class: "modal__foot" }, cancel, ok));
    let result = false;
    cancel.addEventListener("click", () => dlg.close());
    ok.addEventListener("click", () => { result = true; dlg.close(); });
    dlg.addEventListener("close", () => { dlg.remove(); resolve(result); });
    document.body.append(dlg);
    dlg.showModal();
    cancel.focus();
  });
}

// ------------------------------------------------------------------ inline states

export function errorBanner(err, onRetry) {
  const msg = (err && err.message) || String(err || "Something went wrong.");
  return el("div", { class: "banner", role: "alert" },
    icon("alert"),
    el("div", { class: "banner__msg", text: msg }),
    onRetry ? el("button", { type: "button", class: "btn btn--sm", on: { click: onRetry } }, icon("refresh"), "Retry") : null);
}

export function banner(kind, message, extra) {
  return el("div", { class: "banner banner--" + kind }, icon(kind === "ok" ? "check" : kind === "warn" ? "alert" : "info"),
    el("div", { class: "banner__msg" }, message), extra || null);
}

export function emptyState({ title, text, action }) {
  return el("div", { class: "empty" },
    el("p", { class: "empty__title", text: title }),
    text ? el("p", { class: "empty__text", text }) : null,
    action || null);
}

/** Skeleton rows for a table body. widths: array of css widths per column. */
export function skeletonRows(widths, rows = 6) {
  const out = [];
  for (let r = 0; r < rows; r++) {
    out.push(el("tr", { "aria-hidden": "true" },
      widths.map((w, i) => el("td", null, el("span", { class: "skel", style: "width:" + (i === 0 ? Math.max(40, 70 - r * 5) + "%" : w) })))));
  }
  return out;
}

export function skeletonBlock(lines = 3) {
  const out = el("div", { "aria-hidden": "true", style: "display:flex;flex-direction:column;gap:10px" });
  for (let i = 0; i < lines; i++) out.append(el("span", { class: "skel", style: "width:" + (90 - i * 17) + "%" }));
  return out;
}

/** Relative time with the exact time on hover. Re-rendered by the page's poll. */
export function timeEl(v) {
  if (!v) return el("span", { class: "muted", text: "never" });
  return el("time", { datetime: String(v), title: exact(v), text: relative(v) });
}

/** A body cell spanning the whole table, for empty/error states inside tables. */
export function fullRow(cols, ...children) {
  return el("tr", { class: "is-dim" }, el("td", { colspan: cols, style: "padding:0" }, ...children));
}

export function setBusy(btn, busy, label) {
  if (busy) {
    btn.dataset.label = btn.textContent;
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
    mount(btn, el("span", { class: "spinner", "aria-hidden": "true" }), label || btn.dataset.label);
  } else {
    btn.disabled = false;
    btn.removeAttribute("aria-busy");
  }
}
