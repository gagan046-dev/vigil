/* System: environment, LLM budget, queue, scheduler, search provider and sellers, version. */
import { el, icon, clear } from "../dom.js";
import { getSystem } from "../store.js";
import { number, plural } from "../format.js";
import { errorBanner, skeletonBlock } from "../components/feedback.js";
import { poll } from "./common.js";

export function render(main, _params, ctx) {
  const grid = el("div", { class: "sys-grid" });
  const bannerBox = el("div");
  main.append(bannerBox, grid);
  grid.append(el("section", { class: "panel" }, el("div", { class: "panel__body" }, skeletonBlock(4))),
    el("section", { class: "panel" }, el("div", { class: "panel__body" }, skeletonBlock(4))));
  let last = null;
  let latency = null;

  async function load() {
    const t0 = performance.now();
    try {
      const s = await getSystem(true);
      latency = Math.round(performance.now() - t0);
      if (!ctx.alive()) return;
      last = s;
      clear(bannerBox);
      paint(s);
    } catch (e) {
      if (!ctx.alive()) return;
      clear(bannerBox);
      bannerBox.append(errorBanner(e, load));
      if (!last) clear(grid);
    }
  }

  function panel(title, rows, extra) {
    return el("section", { class: "panel" },
      el("div", { class: "panel__head" }, el("h2", { class: "panel__title", text: title })),
      el("div", { class: "panel__body" },
        el("dl", { class: "facts" }, rows.filter(Boolean).map(([k, v]) => [el("dt", { text: k }), el("dd", null, v)])),
        extra || null));
  }

  function paint(s) {
    clear(grid);
    const used = Number(s.llm_budget_used), cap = Number(s.llm_daily_budget);
    const hasBudget = Number.isFinite(used) && Number.isFinite(cap) && cap > 0;
    const ratio = hasBudget ? Math.min(1, used / cap) : 0;
    const meter = hasBudget ? el("div", { class: "budget" },
      el("div", { class: "budget__row" }, el("span", { text: "Today’s LLM calls" }), el("span", { class: "mono", text: number(used) + " / " + number(cap) })),
      el("div", { class: "meter", role: "meter", "aria-valuemin": "0", "aria-valuemax": String(cap), "aria-valuenow": String(used), "aria-label": "LLM budget used" },
        el("div", { class: "meter__bar", style: "width:" + (ratio * 100).toFixed(1) + "%", "data-level": ratio >= 0.9 ? "danger" : ratio >= 0.7 ? "warn" : "ok" })),
      el("p", { class: "field__hint", text: ratio >= 1 ? "Budget exhausted: Vigil falls back to rules until midnight UTC." : "Resets daily. When it runs out, Vigil uses rules only." })) : null;

    const sellers = (Array.isArray(s.compare_sellers) ? s.compare_sellers : String(s.compare_sellers || "").split(",").map((x) => x.trim()).filter(Boolean))
      .map((x) => (x && typeof x === "object" ? x : { domain: String(x) }));
    const depth = s.queue_depth;
    const theme = el("button", { type: "button", class: "btn btn--sm", on: { click: () => window.dispatchEvent(new CustomEvent("vigil:toggle-theme")) } }, icon("moon", "icon--sm"), "Toggle theme");

    grid.append(
      panel("Service", [
        ["Environment", el("span", { class: "tag", text: s.env || "—" })],
        ["Version", el("span", { class: "mono", text: s.version || "—" })],
        ["API latency", el("span", { class: "mono", text: latency !== null ? latency + " ms" : "—" })],
        ["Interface", theme],
      ]),
      panel("Language model", [
        ["Mode", el("span", { class: "tag", text: s.llm_mode || "—" })],
      ], meter),
      panel("Work queue", [
        ["Pending jobs", el("span", { class: "mono" + (Number(depth) > 20 ? " text-warn" : ""), text: depth === null || depth === undefined ? "unavailable" : number(depth) })],
        ["Scheduler", el("span", { class: "tag", text: s.scheduler || "—" })],
        ["Minimum interval", s.min_interval_minutes ? plural(s.min_interval_minutes, "minute") : "—"],
      ], el("p", { class: "field__hint sys-hint", text: "Workers scale with this queue; checks and comparisons share it." })),
      panel("Price comparison", [
        ["Search provider", el("span", { class: "tag", text: s.search_provider || "—" })],
        ["Sellers", sellers.length ? plural(sellers.length, "domain") : "—"],
      ], sellers.length ? el("ul", { class: "seller-list", role: "list" }, sellers.map((d) => el("li", null, d.name ? el("span", { text: d.name + " " }) : null, el("span", { class: "mono muted", text: d.domain || "" })))) : null));
  }

  load();
  return poll(load, 15000);
}
