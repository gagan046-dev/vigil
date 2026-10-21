/* Row-action popover menu with keyboard support (arrows, Home/End, Escape, Tab closes). */
import { el, icon } from "../dom.js";

let current = null;

function closeCurrent(focusAnchor) {
  if (!current) return;
  const { node, anchor, cleanup } = current;
  current = null;
  cleanup();
  node.remove();
  anchor.setAttribute("aria-expanded", "false");
  if (focusAnchor) anchor.focus();
}

/** items: [{label, icon, danger, onSelect} | "sep"] */
export function openMenu(anchor, items) {
  if (current && current.anchor === anchor) { closeCurrent(true); return; }
  closeCurrent(false);
  const node = el("div", { class: "menu", role: "menu" });
  const buttons = [];
  for (const it of items) {
    if (it === "sep") { node.append(el("div", { class: "menu__sep", role: "separator" })); continue; }
    const b = el("button", { type: "button", role: "menuitem", tabindex: "-1", class: "menu__item" + (it.danger ? " menu__item--danger" : "") },
      it.icon ? icon(it.icon) : null, it.label);
    b.addEventListener("click", () => { closeCurrent(false); it.onSelect(); });
    buttons.push(b);
    node.append(b);
  }
  document.body.append(node);
  const r = anchor.getBoundingClientRect();
  const w = node.offsetWidth, h = node.offsetHeight;
  let left = Math.min(r.right - w, window.innerWidth - w - 8);
  left = Math.max(8, left);
  let top = r.bottom + 4;
  if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 4);
  node.style.left = left + "px";
  node.style.top = top + "px";
  anchor.setAttribute("aria-expanded", "true");

  const focusAt = (i) => buttons[(i + buttons.length) % buttons.length].focus();
  const onKey = (e) => {
    const i = buttons.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); focusAt(i + 1); }
    else if (e.key === "ArrowUp") { e.preventDefault(); focusAt(i - 1); }
    else if (e.key === "Home") { e.preventDefault(); focusAt(0); }
    else if (e.key === "End") { e.preventDefault(); focusAt(buttons.length - 1); }
    else if (e.key === "Escape") { e.preventDefault(); closeCurrent(true); }
    else if (e.key === "Tab") { closeCurrent(false); }
  };
  const onDown = (e) => { if (!node.contains(e.target) && e.target !== anchor && !anchor.contains(e.target)) closeCurrent(false); };
  const onScroll = () => closeCurrent(false);
  node.addEventListener("keydown", onKey);
  document.addEventListener("mousedown", onDown, true);
  window.addEventListener("resize", onScroll);
  window.addEventListener("scroll", onScroll, true);
  current = {
    node, anchor,
    cleanup: () => {
      document.removeEventListener("mousedown", onDown, true);
      window.removeEventListener("resize", onScroll);
      window.removeEventListener("scroll", onScroll, true);
    },
  };
  focusAt(0);
}

export function closeMenus() { closeCurrent(false); }

export function menuButton(label, items) {
  const b = el("button", { type: "button", class: "icon-btn", "aria-label": label, "aria-haspopup": "menu", "aria-expanded": "false" }, icon("more"));
  b.addEventListener("click", (e) => { e.stopPropagation(); openMenu(b, typeof items === "function" ? items() : items); });
  return b;
}

// ------------------------------------------------------------------ tooltip

let tipNode = null;
export function attachTip(target, text) {
  const show = () => {
    hideTip();
    const t = typeof text === "function" ? text() : text;
    if (!t) return;
    tipNode = el("div", { class: "tip", role: "tooltip", text: t });
    document.body.append(tipNode);
    const r = target.getBoundingClientRect();
    const w = tipNode.offsetWidth, h = tipNode.offsetHeight;
    let left = Math.min(Math.max(8, r.left + r.width / 2 - w / 2), window.innerWidth - w - 8);
    let top = r.top - h - 6;
    if (top < 8) top = r.bottom + 6;
    tipNode.style.left = left + "px";
    tipNode.style.top = top + "px";
  };
  target.addEventListener("mouseenter", show);
  target.addEventListener("focus", show);
  target.addEventListener("mouseleave", hideTip);
  target.addEventListener("blur", hideTip);
}
export function hideTip() { if (tipNode) { tipNode.remove(); tipNode = null; } }
