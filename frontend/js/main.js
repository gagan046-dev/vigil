/* Vigil UI: hash router, shell (sidebar, top bar, badge, theme), page lifecycle. */
import { el, icon, mount, clear } from "./dom.js";
import { api, onConnection } from "./api.js";
import { getSystem } from "./store.js";
import { openNewWatch } from "./components/new-watch.js";
import { closeMenus, hideTip } from "./components/menu.js";
import * as watches from "./pages/watches.js";
import * as watch from "./pages/watch.js";
import * as compare from "./pages/compare.js";
import * as compareRun from "./pages/compare-run.js";
import * as inbox from "./pages/inbox.js";
import * as activity from "./pages/activity.js";
import * as system from "./pages/system.js";

const ROUTES = [
  { re: /^\/watches\/?$/, nav: "watches", page: watches, title: "Watches" },
  { re: /^\/watches\/([^/]+)$/, nav: "watches", page: watch, title: "Watch" },
  { re: /^\/compare\/?$/, nav: "compare", page: compare, title: "Compare prices" },
  { re: /^\/compare\/([^/]+)$/, nav: "compare", page: compareRun, title: "Comparison" },
  { re: /^\/inbox\/?$/, nav: "inbox", page: inbox, title: "Inbox" },
  { re: /^\/activity\/?$/, nav: "activity", page: activity, title: "Activity" },
  { re: /^\/system\/?$/, nav: "system", page: system, title: "System" },
];

const main = document.getElementById("main");
const titleEl = document.getElementById("page-title");
const crumbsEl = document.getElementById("crumbs");
let cleanup = null;
let renderId = 0;

const ctx = {
  /** setTitle("Aurora 15", [{label:"Watches", href:"#/watches"}]) */
  setTitle(title, crumbs) {
    titleEl.textContent = title;
    document.title = title + " · Vigil";
    clear(crumbsEl);
    if (crumbs && crumbs.length) {
      crumbs.forEach((c, i) => {
        if (i) crumbsEl.append(icon("chev-right"));
        crumbsEl.append(c.href ? el("a", { href: c.href, text: c.label }) : el("span", { text: c.label }));
      });
      crumbsEl.hidden = false;
    } else crumbsEl.hidden = true;
  },
  isCurrent(id) { return id === renderId; },
};

function route() {
  const hash = location.hash.replace(/^#/, "") || "/watches";
  const path = hash.split("?")[0];
  if (cleanup) { try { cleanup(); } catch (_) { /* ignore */ } cleanup = null; }
  closeMenus(); hideTip();
  let match = null, params = [];
  for (const r of ROUTES) { const m = path.match(r.re); if (m) { match = r; params = m.slice(1).map(decodeURIComponent); break; } }
  if (!match) { location.replace("#/watches"); return; }
  for (const a of document.querySelectorAll("[data-nav]")) {
    if (a.dataset.nav === match.nav) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
  }
  ctx.setTitle(match.title);
  clear(main);
  const id = ++renderId;
  const pageCtx = Object.assign({}, ctx, { id, alive: () => id === renderId });
  cleanup = match.page.render(main, params, pageCtx) || null;
  if (!routeIsFirst) titleEl.focus({ preventScroll: true });
  routeIsFirst = false;
  window.scrollTo(0, 0);
}
let routeIsFirst = true;

// ------------------------------------------------------------------ unread badge

const badge = document.getElementById("unread-badge");
const inboxLink = document.querySelector('[data-nav="inbox"]');
async function refreshUnread() {
  try {
    const r = await api.unreadCount();
    const n = Number(r && r.count) || 0;
    badge.hidden = n === 0;
    badge.textContent = n > 99 ? "99+" : String(n);
    inboxLink.setAttribute("aria-label", n ? "Inbox, " + n + " unread" : "Inbox");
  } catch (_) { /* connection indicator covers this */ }
}
window.addEventListener("vigil:inbox", refreshUnread);

// ------------------------------------------------------------------ connection

const conn = document.getElementById("conn");
const connLabel = conn.querySelector(".conn__label");
onConnection((ok) => {
  conn.dataset.state = ok ? "ok" : "down";
  connLabel.textContent = ok ? "Connected" : "API unreachable";
  conn.title = ok ? "Connected to the Vigil API" : "Cannot reach the Vigil API";
});

// ------------------------------------------------------------------ theme + sidebar

const root = document.documentElement;
const themeBtn = document.getElementById("theme-toggle");
function effectiveTheme() {
  const t = root.getAttribute("data-theme");
  if (t) return t;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
function paintThemeBtn() {
  const dark = effectiveTheme() === "dark";
  mount(themeBtn, icon(dark ? "sun" : "moon"), el("span", { class: "nav__label", text: dark ? "Light theme" : "Dark theme" }));
  themeBtn.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
}
export function toggleTheme() {
  const next = effectiveTheme() === "dark" ? "light" : "dark";
  root.setAttribute("data-theme", next);
  try { localStorage.setItem("vigil.theme", next); } catch (_) { /* ignore */ }
  paintThemeBtn();
  window.dispatchEvent(new CustomEvent("vigil:theme"));
}
themeBtn.addEventListener("click", toggleTheme);
window.addEventListener("vigil:toggle-theme", toggleTheme);
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", paintThemeBtn);
paintThemeBtn();

const sideBtn = document.getElementById("sidebar-toggle");
function paintSide() {
  const c = root.getAttribute("data-sidebar") === "collapsed";
  sideBtn.setAttribute("aria-pressed", String(c));
  sideBtn.setAttribute("aria-label", c ? "Expand sidebar" : "Collapse sidebar");
  for (const a of document.querySelectorAll(".nav__item")) {
    const label = a.querySelector(".nav__label");
    if (c && label) a.title = label.textContent; else a.removeAttribute("title");
  }
}
sideBtn.addEventListener("click", () => {
  const c = root.getAttribute("data-sidebar") === "collapsed";
  if (c) root.removeAttribute("data-sidebar"); else root.setAttribute("data-sidebar", "collapsed");
  try { localStorage.setItem("vigil.sidebar", c ? "expanded" : "collapsed"); } catch (_) { /* ignore */ }
  paintSide();
});
paintSide();

// ------------------------------------------------------------------ global keys

document.getElementById("btn-new-watch").addEventListener("click", () => openNewWatch());
document.addEventListener("keydown", (e) => {
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey) return;
  const t = e.target;
  const typing = t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  if (typing || document.querySelector("dialog[open]")) return;
  if (e.key === "/") {
    const s = main.querySelector("[data-search]");
    if (s) { e.preventDefault(); s.focus(); s.select(); }
  } else if (e.key === "n") {
    e.preventDefault(); openNewWatch();
  }
});

window.addEventListener("hashchange", route);
route();
refreshUnread();
setInterval(refreshUnread, 20000);
getSystem().catch(() => null);
