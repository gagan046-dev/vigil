/* Watch detail: header, value-history chart, key facts, paginated check history. */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { conditionText, everyLabel, money, isNum, isPriceKind, duration, exact, relative, nextRun, hostOf, number } from "../format.js";
import { errorBanner, emptyState, skeletonRows, skeletonBlock, timeEl, fullRow } from "../components/feedback.js";
import { pager } from "../components/pager.js";
import { errandChip, checkChip, valueText, errandState } from "../components/status.js";
import { historyChart } from "../components/chart.js";
import { checkNow, setActive, remove, poll } from "./common.js";

const RANGES = [[7, "7 days"], [30, "30 days"], [90, "90 days"]];

export function render(main, [id], ctx) {
  const st = { e: null, err: null, days: 30, hist: null, histErr: null, checks: null, checksErr: null, cpage: 1, cper: 10 };

  const head = el("div", { class: "detail-head" });
  const bannerBox = el("div");
  const stats = el("div", { class: "stats" });
  const chartBody = el("div", { class: "panel__body chart-body" });
  const rangeSeg = el("div", { class: "segmented", role: "group", "aria-label": "History range" });
  const facts = el("div", { class: "panel__body" });
  const ctbody = el("tbody");
  const cpager = el("div");

  const chartPanel = el("section", { class: "panel", "aria-labelledby": "h-hist" },
    el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-hist", text: "Value history" }), rangeSeg),
    chartBody);
  const checksPanel = el("section", { class: "panel", "aria-labelledby": "h-checks" },
    el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-checks", text: "Check history" })),
    el("div", { class: "table-wrap" }, el("table", { class: "table table--compact" },
      el("thead", null, el("tr", null,
        el("th", { scope: "col", text: "Time" }), el("th", { scope: "col", text: "Result" }),
        el("th", { scope: "col", class: "num", text: "Value" }), el("th", { scope: "col", class: "num col-md", text: "Took" }),
        el("th", { scope: "col", class: "col-md", text: "Path" }), el("th", { scope: "col", class: "col-lg", text: "Message" }))),
      ctbody)),
    cpager);

  main.append(head, bannerBox,
    el("div", { class: "detail-grid" },
      el("div", { class: "detail-main" }, stats, chartPanel, checksPanel),
      el("aside", { class: "detail-side" }, el("section", { class: "panel", "aria-labelledby": "h-facts" },
        el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-facts", text: "Details" })), facts))));

  ctx.setTitle("Watch", [{ label: "Watches", href: "#/watches" }]);
  head.append(skeletonBlock(2));
  facts.append(skeletonBlock(6));
  ctbody.append(...skeletonRows(["50%", "40%", "40%", "30%", "30%", "60%"], 5));

  for (const [d, label] of RANGES) {
    const b = el("button", { type: "button", class: "segmented__btn", text: label, "aria-pressed": String(d === st.days) });
    b.addEventListener("click", () => {
      st.days = d;
      for (const x of rangeSeg.children) x.setAttribute("aria-pressed", String(x === b));
      loadHistory();
    });
    rangeSeg.append(b);
  }

  async function loadErrand() {
    try {
      const e = await api.errand(id);
      if (!ctx.alive()) return;
      st.e = e; st.err = null;
    } catch (err) {
      if (!ctx.alive()) return;
      st.err = err;
    }
    paintHead(); paintFacts(); paintStats();
  }

  async function loadHistory() {
    if (!st.hist) mount(chartBody, el("div", { class: "chart-skel" }, el("span", { class: "skel" })));
    try {
      const h = await api.history(id, st.days);
      if (!ctx.alive()) return;
      st.hist = Array.isArray(h) ? h : (h && h.items) || []; st.histErr = null;
    } catch (err) { if (!ctx.alive()) return; st.histErr = err; }
    paintChart(); paintStats();
  }

  async function loadChecks(silent) {
    if (!silent && st.checks) { clear(ctbody); ctbody.append(...skeletonRows(["50%", "40%", "40%", "30%", "30%", "60%"], Math.min(st.cper, 8))); }
    try {
      const c = await api.errandChecks(id, { page: st.cpage, per_page: st.cper });
      if (!ctx.alive()) return;
      st.checks = c; st.checksErr = null;
    } catch (err) { if (!ctx.alive()) return; st.checksErr = err; }
    paintChecks();
  }

  function paintHead() {
    clear(head);
    if (st.err && !st.e) {
      ctx.setTitle(st.err.status === 404 ? "Not found" : "Watch", [{ label: "Watches", href: "#/watches" }]);
      head.append(st.err.status === 404
        ? emptyState({ title: "This watch no longer exists", text: "It may have been deleted.", action: el("a", { class: "btn", href: "#/watches", text: "Back to watches" }) })
        : errorBanner(st.err, loadErrand));
      chartPanel.hidden = true; checksPanel.hidden = st.err.status === 404;
      main.querySelector(".detail-side").hidden = true;
      return;
    }
    const e = st.e;
    const spec = e.spec || {};
    ctx.setTitle(e.name || spec.name || "Watch", [{ label: "Watches", href: "#/watches" }]);
    const paused = e.active === false;
    const bCheck = el("button", { type: "button", class: "btn" }, icon("refresh"), "Check now");
    bCheck.addEventListener("click", () => checkNow(e, () => { loadErrand(); loadChecks(true); }));
    const bPause = el("button", { type: "button", class: "btn" }, icon(paused ? "play" : "pause"), paused ? "Resume" : "Pause");
    bPause.addEventListener("click", () => setActive(e, paused, loadErrand));
    const bDel = el("button", { type: "button", class: "btn btn--ghost btn--danger-text" }, icon("trash"), "Delete");
    bDel.addEventListener("click", () => remove(e, () => { location.hash = "#/watches"; }));
    head.append(
      el("div", { class: "detail-head__main" },
        el("div", { class: "detail-head__cond" },
          el("span", { class: "detail-head__condition", text: conditionText(spec) || e.condition }),
          errandChip(e)),
        el("p", { class: "detail-head__meta" },
          spec.kind === "best_price_below" ? "Across sellers" : hostOf(spec.url),
          " · ", everyLabel(spec.every_minutes),
          " · last checked ", timeEl(e.last_checked_at))),
      el("div", { class: "detail-head__actions" }, bCheck, bPause, bDel));
    if (errandState(e) === "failing") {
      clear(bannerBox);
      bannerBox.append(el("div", { class: "banner", role: "alert" }, icon("alert"),
        el("div", { class: "banner__msg", text: "The last " + e.consecutive_failures + " checks failed. See the messages below; the page may have moved or be blocking automated requests." })));
    } else clear(bannerBox);
  }

  function paintStats() {
    clear(stats);
    if (!st.e) return;
    const spec = st.e.spec || {};
    const priced = isPriceKind(spec.kind);
    const vals = (st.hist || []).map((p) => p.value).filter(isNum).map(Number);
    const errs = (st.hist || []).filter((p) => p.status === "error").length;
    const tile = (label, value, sub, cls) => el("div", { class: "stat" + (cls ? " " + cls : "") },
      el("p", { class: "stat__label", text: label }), el("p", { class: "stat__value", text: value }), sub ? el("p", { class: "stat__sub", text: sub }) : null);
    if (priced) {
      stats.append(
        tile("Current", valueText(st.e.last_value, spec.kind), st.e.last_checked_at ? relative(st.e.last_checked_at) : "not checked yet"),
        tile("Target", isNum(spec.threshold) ? money(spec.threshold) : "Any drop", spec.kind === "price_above" ? "alert when above" : "alert when below"),
        tile("Lowest, " + st.days + " d", vals.length ? money(Math.min(...vals)) : "—", vals.length ? "highest " + money(Math.max(...vals)) : null),
        tile("Checks, " + st.days + " d", st.hist ? number(st.hist.length) : "—", st.hist ? (errs ? errs + " failed" : "none failed") : null, errs ? "stat--warn" : ""));
    } else {
      const trig = (st.hist || []).filter((p) => p.status === "triggered").length;
      stats.append(
        tile("Condition", st.e.condition_met ? "Met" : "Not met", st.e.last_checked_at ? "as of " + relative(st.e.last_checked_at) : "not checked yet"),
        tile("Alerts, " + st.days + " d", st.hist ? number(trig) : "—"),
        tile("Checks, " + st.days + " d", st.hist ? number(st.hist.length) : "—", st.hist ? (errs ? errs + " failed" : "none failed") : null, errs ? "stat--warn" : ""));
    }
  }

  function paintChart() {
    clear(chartBody);
    const spec = (st.e && st.e.spec) || {};
    if (st.histErr) { chartBody.append(errorBanner(st.histErr, loadHistory)); return; }
    if (st.e && !isPriceKind(spec.kind)) {
      chartPanel.querySelector(".panel__title").textContent = "Results over time";
      chartBody.append(resultStrip(st.hist || []));
      return;
    }
    const pts = st.hist || [];
    if (!pts.some((p) => isNum(p.value))) {
      chartBody.append(emptyState({ title: "No values in the last " + st.days + " days", text: "Values appear here after the first successful check." }));
      return;
    }
    chartBody.append(historyChart(pts, { target: isNum(spec.threshold) ? Number(spec.threshold) : null }));
  }

  function resultStrip(pts) {
    if (!pts.length) return emptyState({ title: "No checks in this period", text: "Results appear here after the first check." });
    const strip = el("div", { class: "strip", role: "img", "aria-label": pts.length + " checks" });
    const last = pts.slice(-120);
    for (const p of last) strip.append(el("span", { class: "strip__cell strip__cell--" + (p.status || "unchanged"), title: exact(p.at) + " · " + (p.status || "") }));
    return el("div", null, strip, el("div", { class: "strip__legend" },
      ["unchanged", "changed", "triggered", "error"].map((s) => el("span", null, el("span", { class: "strip__cell strip__cell--" + s }), s[0].toUpperCase() + s.slice(1)))));
  }

  function paintFacts() {
    clear(facts);
    if (!st.e) return;
    const e = st.e, s = e.spec || {};
    const row = (k, v) => [el("dt", { text: k }), el("dd", null, v)];
    const srvNext = e.next_run_at ? new Date(e.next_run_at) : null;
    const next = e.active === false ? "Paused" : srvNext && !isNaN(srvNext) ? srvNext : nextRun(s.every_minutes);
    const lc = e.latest_compare;
    facts.append(el("dl", { class: "facts facts--stack" },
      s.url ? row("URL", el("a", { class: "url", href: s.url, target: "_blank", rel: "noopener noreferrer", text: s.url })) : null,
      s.query ? row("Product", s.query) : null,
      s.kind !== "best_price_below" ? row("Selector", s.selector ? el("code", { class: "url", text: s.selector }) : el("span", { class: "muted", text: "Automatic" })) : null,
      s.kind !== "best_price_below" ? row("Learned selector", e.selector_learned ? el("code", { class: "url", text: e.selector_learned }) : el("span", { class: "muted", text: "Not learned yet" })) : null,
      s.kind !== "best_price_below" ? row("Rendering", s.render ? "Runs JavaScript" : "Plain HTML") : null,
      row("Schedule", el("span", null, everyLabel(s.every_minutes), " ", el("code", { class: "muted", text: e.schedule || "" }))),
      row("Next run", typeof next === "string" ? next : el("time", { datetime: next.toISOString(), title: exact(next), text: relative(next) + (srvNext ? "" : " (estimated)") })),
      lc && lc.id ? row("Latest comparison", el("a", { href: "#/compare/" + encodeURIComponent(lc.id), text: lc.best && isNum(lc.best.price) ? money(lc.best.price, lc.best.currency) + " at " + (lc.best.seller_name || hostOf(lc.best.url)) : "Open results" })) : null,
      row("Failures in a row", el("span", { class: Number(e.consecutive_failures) ? "text-danger" : "", text: String(e.consecutive_failures || 0) })),
      row("Created", el("time", { datetime: e.created_at || "", title: exact(e.created_at), text: e.created_at ? relative(e.created_at) : "—" })),
      e.prompt ? row("Original request", el("span", { class: "prompt-quote", text: e.prompt })) : null));
  }

  function paintChecks() {
    clear(ctbody); clear(cpager);
    if (st.checksErr && !st.checks) { ctbody.append(fullRow(6, el("div", { class: "pad" }, errorBanner(st.checksErr, () => loadChecks())))); return; }
    const d = st.checks;
    if (!d) return;
    if (!d.items.length) { ctbody.append(fullRow(6, emptyState({ title: "No checks yet", text: "The first check runs right after a watch is saved. Use “Check now” to run one." }))); return; }
    const kind = st.e && st.e.spec ? st.e.spec.kind : undefined;
    for (const c of d.items) {
      ctbody.append(el("tr", null,
        el("td", { class: "nowrap" }, el("time", { datetime: c.at, title: exact(c.at), text: relative(c.at) })),
        el("td", null, checkChip(c.status)),
        el("td", { class: "num value-plain" }, valueText(c.value, kind)),
        el("td", { class: "num col-md muted" }, duration(c.duration_ms)),
        el("td", { class: "col-md nowrap" }, c.rendered ? el("span", { class: "tag", text: "JS" }) : null, " ", c.llm_used ? el("span", { class: "tag", text: "LLM" }) : null),
        el("td", { class: "col-lg msg" }, el("span", { class: "cell-clip", title: c.message || "", text: c.message || "" }))));
    }
    cpager.append(pager(d, (p, per) => { st.cpage = p; st.cper = per; loadChecks(); }, { noun: "checks" }));
  }

  loadErrand();
  loadHistory();
  loadChecks();
  const stop = poll(() => { loadErrand(); if (st.cpage === 1) loadChecks(true); }, 10000);
  return stop;
}
