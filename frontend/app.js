/* Vigil frontend — the night-watch logbook.
   Vanilla JS, no build step. All text coming from the API or the LLM is written with
   textContent / DOM nodes only — never innerHTML. */
(function () {
  "use strict";

  const POLL_MS = 5000;
  const DRYRUN_CLIENT_TIMEOUT_MS = 75000;
  const DEFAULT_TIMEOUT_MS = 15000;
  const ALLOWED_EVERY = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440];
  const PRICE_KINDS = ["price_below", "price_above"];
  const TEXT_KINDS = ["text_appears", "text_disappears"];

  const state = {
    system: null,
    errands: [],
    errandsById: new Map(),
    plan: null,          // last PlanResult
    planPrompt: "",
    expanded: null,      // errand id whose history is open
    history: [],         // checks for the expanded errand
    flashes: new Map(),  // errand id -> short message
    pollTimer: null,
    busy: new Set(),     // errand ids with an action in flight
    online: null,
  };

  // ------------------------------------------------------------------ helpers

  const $ = (id) => document.getElementById(id);

  /** Build an element. attrs: {class, text, ...attributes}; children: nodes or strings. */
  function el(tag, attrs, ...children) {
    const node = document.createElement(tag);
    if (attrs) {
      for (const [k, v] of Object.entries(attrs)) {
        if (v === undefined || v === null || v === false) continue;
        if (k === "class") node.className = v;
        else if (k === "text") node.textContent = String(v);
        else if (k === "dataset") Object.assign(node.dataset, v);
        else node.setAttribute(k, v === true ? "" : String(v));
      }
    }
    for (const c of children) {
      if (c === null || c === undefined || c === false) continue;
      node.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return node;
  }

  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  function pad2(n) { return String(n).padStart(2, "0"); }

  function parseTime(v) {
    if (!v) return null;
    const d = new Date(v);
    return isNaN(d.getTime()) ? null : d;
  }

  /** "0815" — the logbook way. Older than today gets a date prefix. */
  function hhmm(v, withDate) {
    const d = parseTime(v);
    if (!d) return "----";
    const t = pad2(d.getHours()) + pad2(d.getMinutes());
    const today = new Date();
    if (withDate || d.toDateString() !== today.toDateString()) {
      const mon = d.toLocaleString("en-GB", { month: "short" });
      return pad2(d.getDate()) + " " + mon + " " + t;
    }
    return t;
  }

  function ago(v) {
    const d = parseTime(v);
    if (!d) return "never";
    const s = Math.max(0, Math.round((Date.now() - d.getTime()) / 1000));
    if (s < 60) return s + " s ago";
    if (s < 3600) return Math.round(s / 60) + " min ago";
    if (s < 86400) return Math.round(s / 3600) + " h ago";
    return Math.round(s / 86400) + " d ago";
  }

  const moneyFmt = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
  function money(v) {
    if (v === null || v === undefined || v === "" || isNaN(Number(v))) return "—";
    return "₹" + moneyFmt.format(Number(v));
  }

  function hostOf(url) {
    try { return new URL(url).host; } catch (_) { return url || ""; }
  }

  function everyLabel(n) {
    n = Number(n);
    if (!n) return "—";
    if (n < 60) return "every " + n + " min";
    if (n === 60) return "hourly";
    if (n === 1440) return "daily at 0800";
    return "every " + n / 60 + " h";
  }

  function cronFor(n) {
    n = Number(n);
    if (n < 60) return "*/" + n + " * * * *";
    if (n === 60) return "0 * * * *";
    if (n === 1440) return "0 8 * * *";
    return "0 */" + n / 60 + " * * *";
  }

  function conditionText(spec) {
    if (!spec) return "";
    switch (spec.kind) {
      case "price_below": return "price below " + money(spec.threshold);
      case "price_above": return "price above " + money(spec.threshold);
      case "text_appears": return "“" + (spec.keyword || "") + "” appears";
      case "text_disappears": return "“" + (spec.keyword || "") + "” disappears";
      case "page_changed": return "page changes";
      default: return spec.kind || "";
    }
  }

  /** Pull a list out of either a bare array or a {items|errands|checks|...: []} envelope. */
  function asList(data) {
    if (Array.isArray(data)) return data;
    if (data && typeof data === "object") {
      for (const k of ["items", "errands", "checks", "notifications", "log", "results", "data"]) {
        if (Array.isArray(data[k])) return data[k];
      }
    }
    return [];
  }

  function detailMessage(body, status) {
    if (body && typeof body === "object") {
      const d = body.detail !== undefined ? body.detail : body.message;
      if (typeof d === "string") return d;
      if (Array.isArray(d)) {
        return d.map((e) => {
          const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body").join(".") : "";
          return (loc ? loc + ": " : "") + (e.msg || JSON.stringify(e));
        }).join("; ");
      }
      if (d) return JSON.stringify(d);
    }
    if (typeof body === "string" && body.trim() && !/^\s*</.test(body)) return body.trim().slice(0, 300);
    if (status === 502 || status === 503) return "The Vigil API is not reachable right now (HTTP " + status + ").";
    if (status === 504) return "The API took too long to answer (HTTP 504).";
    return "HTTP " + status;
  }

  class ApiError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  async function api(method, path, body, timeoutMs) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeoutMs || DEFAULT_TIMEOUT_MS);
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: ctrl.signal,
        cache: "no-store",
        credentials: "same-origin",
      });
    } catch (err) {
      clearTimeout(timer);
      if (err && err.name === "AbortError") throw new ApiError("No answer from the API in time.", 0);
      throw new ApiError("Cannot reach the API.", 0);
    }
    clearTimeout(timer);
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch (_) { data = text; } }
    if (!res.ok) throw new ApiError(detailMessage(data, res.status), res.status);
    return data;
  }

  /** A ruled message line: tag column + text. kind: "err" | "ok" | "" */
  function line(kind, tag, text) {
    return el("div", { class: "line" + (kind ? " line--" + kind : ""), role: kind === "err" ? "alert" : null },
      el("span", { class: "line__tag", text: tag }),
      el("span", { text }));
  }

  function showMsg(container, kind, tag, text) {
    clear(container);
    if (text) container.append(line(kind, tag, text));
  }

  function nowTag() { return hhmm(new Date().toISOString()); }

  // ------------------------------------------------------------------ clock

  const WATCHES = [
    [0, 4, "Middle watch"], [4, 8, "Morning watch"], [8, 12, "Forenoon watch"],
    [12, 16, "Afternoon watch"], [16, 18, "First dog watch"], [18, 20, "Last dog watch"],
    [20, 24, "First watch"],
  ];

  function tickClock() {
    const d = new Date();
    const h = d.getHours(), m = d.getMinutes();
    $("clock-time").textContent = pad2(h) + pad2(m);
    const w = WATCHES.find(([a, b]) => h >= a && h < b);
    // ship's bells: one per half hour into the 4-hour watch, 8 at the end
    let bells = ((h % 4) * 2) + Math.floor(m / 30);
    if (bells === 0) bells = 8;
    $("clock-watch").textContent = (w ? w[2] : "") + ", " + bells + (bells === 1 ? " bell" : " bells");
  }

  // ------------------------------------------------------------------ system strip

  function renderSystem() {
    const s = state.system;
    const link = $("sys-link");
    link.textContent = state.online === false ? "lost" : state.online ? "steady" : "connecting";
    link.className = "link-state " + (state.online === false ? "is-down" : state.online ? "is-ok" : "");
    if (!s) return;
    $("sys-env").textContent = s.env || "—";
    $("sys-llm").textContent = s.llm_mode || "—";
    const used = s.llm_budget_used ?? "—", cap = s.llm_daily_budget ?? "—";
    $("sys-budget").textContent = used + "/" + cap;
    $("sys-queue").textContent = s.queue_depth ?? "—";
    $("sys-sched").textContent = s.scheduler || "—";
    $("sys-version").textContent = s.version ? "Vigil " + s.version : "";
    fillEverySelect();
  }

  function minInterval() {
    const m = state.system && Number(state.system.min_interval_minutes);
    return m && m > 0 ? m : 1;
  }

  function fillEverySelect(selected) {
    const sel = $("f-every");
    const current = selected !== undefined ? Number(selected) : Number(sel.value) || 60;
    const allowed = ALLOWED_EVERY.filter((n) => n >= minInterval());
    const key = allowed.join(",");
    if (sel.dataset.key !== key) {
      clear(sel);
      for (const n of allowed) sel.append(el("option", { value: n, text: everyLabel(n) }));
      sel.dataset.key = key;
    }
    const pick = allowed.includes(current) ? current : (allowed.find((n) => n >= current) || allowed[allowed.length - 1]);
    sel.value = String(pick);
  }

  // ------------------------------------------------------------------ post a watch

  async function onPlan(ev) {
    ev.preventDefault();
    const text = $("prompt").value.trim();
    const msg = $("post-msg");
    if (!text) {
      showMsg(msg, "err", "ERR", "Write your orders first — a page and what to watch for.");
      $("prompt").focus();
      return;
    }
    const btn = $("btn-plan");
    btn.disabled = true;
    btn.textContent = "Planning…";
    showMsg(msg, "", nowTag(), "Reading your orders…");
    try {
      const res = await api("POST", "/api/plan", { text }, 45000);
      state.plan = res;
      state.planPrompt = text;
      clear(msg);
      showSpec(res);
    } catch (err) {
      showMsg(msg, "err", "ERR", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Plan";
    }
  }

  function showSpec(res) {
    const spec = (res && res.spec) || {};
    $("f-name").value = spec.name || "";
    $("f-kind").value = spec.kind || "page_changed";
    $("f-url").value = spec.url || "";
    $("f-threshold").value = spec.threshold != null ? String(spec.threshold) : "";
    $("f-keyword").value = spec.keyword || "";
    $("f-selector").value = spec.selector || "";
    $("f-render").checked = !!spec.render;
    fillEverySelect(spec.every_minutes || 60);
    syncKindRows();

    $("plan-source").textContent = res && res.source === "llm" ? "drafted by the model" : "parsed by rule";
    const notes = $("plan-notes");
    clear(notes);
    for (const n of asList(res && res.notes)) notes.append(el("li", { text: n }));

    clear($("dry-out"));
    for (const id of ["f-url", "f-threshold", "f-keyword", "f-name"]) $(id).removeAttribute("aria-invalid");
    const form = $("spec-form");
    form.hidden = false;
    $("f-name").focus();
  }

  function syncKindRows() {
    const kind = $("f-kind").value;
    document.querySelector('tr[data-for="price"]').hidden = !PRICE_KINDS.includes(kind);
    document.querySelector('tr[data-for="text"]').hidden = !TEXT_KINDS.includes(kind);
  }

  /** Accepts "1,20,000", "₹120000", "1.2 lakh", "120k". Returns a number or NaN. */
  function parseAmount(raw) {
    let s = String(raw || "").toLowerCase().replace(/[₹$€£,\s]|rs\.?|inr/g, "");
    let mult = 1;
    if (/lakhs?$|lacs?$/.test(s)) { mult = 100000; s = s.replace(/lakhs?$|lacs?$/, ""); }
    else if (/k$/.test(s)) { mult = 1000; s = s.slice(0, -1); }
    if (!/^\d+(\.\d+)?$/.test(s)) return NaN;
    return Number(s) * mult;
  }

  /** Read and validate the spec table. Returns {spec} or {error, field}. */
  function readSpec() {
    const kind = $("f-kind").value;
    const spec = {
      name: $("f-name").value.trim(),
      url: $("f-url").value.trim(),
      kind,
      threshold: null,
      keyword: null,
      selector: $("f-selector").value.trim() || null,
      render: $("f-render").checked,
      every_minutes: Number($("f-every").value) || 60,
    };
    for (const id of ["f-url", "f-threshold", "f-keyword", "f-name"]) $(id).removeAttribute("aria-invalid");
    if (!spec.name) spec.name = hostOf(spec.url) + ": " + conditionText({ ...spec, threshold: parseAmount($("f-threshold").value), keyword: $("f-keyword").value.trim() });
    if (spec.name.length > 80) spec.name = spec.name.slice(0, 80);
    if (!/^https?:\/\/[^\s/]+/i.test(spec.url)) return { error: "The url must start with http:// or https://", field: "f-url" };
    if (PRICE_KINDS.includes(kind)) {
      const t = parseAmount($("f-threshold").value);
      if (!(t > 0)) return { error: "A price watch needs a threshold above zero.", field: "f-threshold" };
      spec.threshold = t;
    }
    if (TEXT_KINDS.includes(kind)) {
      const k = $("f-keyword").value.trim();
      if (!k) return { error: "A text watch needs the words to look for.", field: "f-keyword" };
      if (k.length > 120) return { error: "Keep the keyword under 120 characters.", field: "f-keyword" };
      spec.keyword = k;
    }
    return { spec };
  }

  function invalid(r, out) {
    showMsg(out, "err", "ERR", r.error);
    const f = $(r.field);
    if (f) { f.setAttribute("aria-invalid", "true"); f.focus(); }
  }

  let dryTimer = null;

  async function onDryRun() {
    const out = $("dry-out");
    const r = readSpec();
    if (r.error) return invalid(r, out);
    const btn = $("btn-dry");
    btn.disabled = true;
    $("btn-keep").disabled = true;
    clear(out);
    const secs = el("span", { class: "mono", text: "0 s" });
    out.append(el("div", { class: "dry__wait" },
      el("span", { class: "sweep", "aria-hidden": "true" }),
      el("span", { text: "Taking a sounding — a worker is fetching the page. This can take up to a minute." }),
      secs));
    const started = Date.now();
    dryTimer = setInterval(() => { secs.textContent = Math.round((Date.now() - started) / 1000) + " s"; }, 1000);
    try {
      const res = await api("POST", "/api/dryrun", { spec: r.spec }, DRYRUN_CLIENT_TIMEOUT_MS);
      renderDry(res, r.spec, Math.round((Date.now() - started) / 1000));
    } catch (err) {
      const m = err.status === 504
        ? "No worker answered within 60 s. The worker may be scaling up from zero — try again in a moment."
        : err.message;
      showMsg(out, "err", "ERR", m);
    } finally {
      clearInterval(dryTimer);
      btn.disabled = false;
      $("btn-keep").disabled = false;
    }
  }

  function renderDry(res, spec, secs) {
    const out = $("dry-out");
    clear(out);
    res = res || {};
    const isPrice = PRICE_KINDS.includes(spec.kind);
    const dl = el("dl", { class: "reading", "aria-label": "Dry run result" });
    const row = (k, v, cls) => { dl.append(el("dt", { text: k }), el("dd", { class: cls || null, text: v })); };

    row("sounding", (res.ok ? "taken" : "failed") + " in " + secs + " s", res.ok ? null : "verdict-false");
    if (isPrice) row("value now", money(res.value), "big");
    else if (res.value != null) row("value now", String(res.value), "big");
    const verdict = res.condition_true ? "TRUE — this would alert you" : "false — still waiting";
    row("condition", verdict, res.condition_true ? "verdict-true" : "verdict-false");
    row("rendered", res.rendered ? "yes, with JavaScript" : "no, plain HTML");
    if (res.selector) row("selector", res.selector);
    if (res.message) row("note", res.message);
    if (res.excerpt) row("excerpt", res.excerpt, "excerpt");
    out.append(dl);

    if (!res.ok) {
      out.append(line("err", "ERR", res.message || "The check failed. Review the url and try again."));
    }
    if (res.rendered && !spec.render) {
      const btn = el("button", { type: "button", class: "btn btn--sm", text: "Use render" });
      btn.addEventListener("click", () => {
        $("f-render").checked = true;
        btn.remove();
        $("f-render").focus();
      });
      out.append(el("div", { class: "line suggest" },
        el("span", { class: "line__tag", text: "HINT" }),
        el("span", null, "This page only showed its content after running JavaScript. Keep render on for reliable checks. ", btn)));
    }
    if (res.selector && !spec.selector) {
      const btn = el("button", { type: "button", class: "btn btn--sm", text: "Use selector" });
      btn.addEventListener("click", () => { $("f-selector").value = res.selector; btn.remove(); $("f-selector").focus(); });
      out.append(el("div", { class: "line suggest" },
        el("span", { class: "line__tag", text: "HINT" }),
        el("span", null, "Found the value at " + res.selector + ". ", btn)));
    }
  }

  async function onKeep(ev) {
    ev.preventDefault();
    const out = $("dry-out");
    const r = readSpec();
    if (r.error) return invalid(r, out);
    const btn = $("btn-keep");
    btn.disabled = true;
    btn.textContent = "Posting…";
    try {
      const created = await api("POST", "/api/errands", { spec: r.spec, prompt: state.planPrompt || null }, 20000);
      const name = (created && (created.name || (created.spec && created.spec.name))) || r.spec.name;
      discardSpec();
      $("prompt").value = "";
      showMsg($("post-msg"), "ok", nowTag(), "Watch posted: " + name + ". The first sounding is queued.");
      await refresh();
      $("prompt").focus();
    } catch (err) {
      showMsg(out, "err", "ERR", err.message);
    } finally {
      btn.disabled = false;
      btn.textContent = "Keep watch";
    }
  }

  function discardSpec() {
    $("spec-form").hidden = true;
    clear($("dry-out"));
    clear($("plan-notes"));
    state.plan = null;
  }

  // ------------------------------------------------------------------ watches

  function lampState(e) {
    if (e.active === false) return "paused";
    if (e.failing === true || e.last_status === "error" || Number(e.consecutive_failures) > 0) return "failing";
    return "active";
  }

  function renderWatches() {
    const list = $("watch-list");
    const focusKey = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.fk : null;
    clear(list);
    const errands = state.errands;
    const active = errands.filter((e) => e.active !== false).length;
    $("watch-count").textContent = errands.length ? active + " on watch · " + (errands.length - active) + " stood down" : "";

    if (!errands.length) {
      list.append(el("li", { class: "empty" }, "No watches posted. The deck is quiet."));
      return;
    }

    for (const e of errands) {
      const spec = e.spec || {};
      const ls = lampState(e);
      const isPrice = PRICE_KINDS.includes(spec.kind);
      const id = String(e.id);
      const busy = state.busy.has(id);
      const lampLabel = ls === "active" ? "On watch" : ls === "paused" ? "Paused" : "Failing";

      let valueText = "—";
      if (isPrice) valueText = money(e.last_value);
      else if (!e.last_status || !e.last_checked_at) valueText = "—";
      else if (spec.kind === "text_appears") valueText = e.condition_met ? "present" : "absent";
      else if (spec.kind === "text_disappears") valueText = e.condition_met ? "gone" : "present";
      else if (spec.kind === "page_changed") valueText = e.last_status === "changed" || e.last_status === "triggered" ? "changed" : "steady";

      const cron = e.cron || e.schedule || e.next_cron || e.schedule_cron || cronFor(spec.every_minutes);

      const btn = (action, label, extra) => el("button", {
        type: "button",
        class: "btn btn--sm" + (extra ? " " + extra : ""),
        dataset: { action, id, fk: id + ":" + action },
        disabled: busy,
        "aria-label": label + " — " + (e.name || spec.name || "watch"),
      }, label);

      const tools = el("div", { class: "watch__tools" },
        btn("check", "Check now"),
        e.active === false ? btn("resume", "Resume") : btn("pause", "Pause"),
        btn("history", state.expanded === id ? "Hide log" : "Log", null),
        btn("delete", "Delete", "btn--danger"),
        state.flashes.has(id) ? el("span", { class: "watch__flash", role: "status", text: state.flashes.get(id) }) : null);
      const histBtn = tools.querySelector('[data-action="history"]');
      histBtn.setAttribute("aria-expanded", state.expanded === id ? "true" : "false");

      const statusCls = "status status--" + (e.last_status || "none");
      const li = el("li", { class: "watch is-" + ls + (e.condition_met ? " is-met" : ""), dataset: { id } },
        el("div", { class: "watch__lamp" },
          el("span", { class: "lamp lamp--" + ls, "aria-hidden": "true" }),
          el("span", { class: "sr-only", text: lampLabel })),
        el("div", { class: "watch__main" },
          el("h3", { class: "watch__name", text: e.name || spec.name || hostOf(spec.url) }),
          el("p", { class: "watch__cond" },
            conditionText(spec) + " · " + everyLabel(spec.every_minutes) + (spec.render ? " · rendered" : "") + " ",
            el("span", { class: "host", title: spec.url || "", text: hostOf(spec.url) }), " ",
            el("span", { class: "cron", title: "CronJob schedule (" + ((state.system && state.system.scheduler) || "scheduler") + ")", text: cron })),
          ls === "failing" && Number(e.consecutive_failures) > 0
            ? el("p", { class: "watch__fail", text: e.consecutive_failures + " failed sounding" + (e.consecutive_failures > 1 ? "s" : "") + " in a row" })
            : null),
        el("div", { class: "watch__reading" },
          el("span", { class: "watch__value", text: valueText }),
          el("span", { class: "watch__when", title: e.last_checked_at || "" },
            el("span", { class: statusCls, text: e.last_status || "not yet" }), " ",
            e.last_checked_at ? hhmm(e.last_checked_at) : "")),
        tools);

      if (state.expanded === id) li.append(renderHistory(spec));
      list.append(li);
    }

    if (focusKey) {
      const target = list.querySelector('[data-fk="' + CSS.escape(focusKey) + '"]');
      if (target) target.focus({ preventScroll: true });
    }
  }

  function renderHistory(spec) {
    const wrap = el("div", { class: "watch__hist" });
    const items = state.history;
    if (!items.length) {
      wrap.append(el("p", { class: "empty empty--sm", text: "No soundings recorded yet." }));
      return wrap;
    }
    const ol = el("ol", { class: "hist mono", "aria-label": "Recent checks for this watch" });
    const isPrice = PRICE_KINDS.includes(spec.kind);
    for (const c of items) {
      ol.append(el("li", null,
        el("span", { class: "t", title: c.at || "", text: hhmm(c.at) }),
        el("span", { class: "s status status--" + (c.status || ""), text: c.status || "" }),
        el("span", { class: "x" },
          c.rendered ? el("span", { class: "flag", title: "rendered with JavaScript", text: "JS" }) : null,
          c.llm_used ? el("span", { class: "flag", title: "used the language model", text: "LLM" }) : null,
          c.duration_ms != null ? el("span", { class: "flag", text: c.duration_ms + " ms" }) : null),
        el("span", { class: "v", text: isPrice || c.value != null ? money(c.value) : "" }),
        c.message ? el("span", { class: "m", text: c.message }) : null));
    }
    wrap.append(ol);
    return wrap;
  }

  async function onWatchAction(ev) {
    const b = ev.target.closest("button[data-action]");
    if (!b) return;
    const id = b.dataset.id, action = b.dataset.action;
    const e = state.errandsById.get(id);
    const name = (e && (e.name || (e.spec && e.spec.name))) || "this watch";
    const msg = $("watch-msg");

    if (action === "history") {
      if (state.expanded === id) { state.expanded = null; state.history = []; renderWatches(); return; }
      state.expanded = id;
      state.history = [];
      renderWatches();
      await loadHistory();
      renderWatches();
      return;
    }
    if (action === "delete" && !window.confirm("Stand down and delete “" + name + "”? Its log will be removed too.")) return;

    state.busy.add(id);
    renderWatches();
    try {
      if (action === "check") {
        await api("POST", "/api/errands/" + encodeURIComponent(id) + "/check");
        flash(id, "sounding queued");
      } else if (action === "pause" || action === "resume") {
        await api("PATCH", "/api/errands/" + encodeURIComponent(id), { active: action === "resume" });
        flash(id, action === "pause" ? "stood down" : "back on watch");
      } else if (action === "delete") {
        await api("DELETE", "/api/errands/" + encodeURIComponent(id));
        if (state.expanded === id) { state.expanded = null; state.history = []; }
        showMsg(msg, "ok", nowTag(), "Deleted: " + name + ".");
      }
      if (action !== "delete") clear(msg);
    } catch (err) {
      showMsg(msg, "err", "ERR", "Could not " + (action === "check" ? "check" : action) + " “" + name + "”: " + err.message);
    } finally {
      state.busy.delete(id);
      await refresh();
      if (action === "delete") $("watches").focus();
    }
  }

  function flash(id, text) {
    state.flashes.set(id, text);
    setTimeout(() => { if (state.flashes.get(id) === text) { state.flashes.delete(id); renderWatches(); } }, 4000);
  }

  async function loadHistory() {
    if (!state.expanded) return;
    try {
      const data = await api("GET", "/api/errands/" + encodeURIComponent(state.expanded) + "/checks?limit=20");
      state.history = asList(data);
    } catch (_) { /* keep what we had; the watch list carries the error line */ }
  }

  // ------------------------------------------------------------------ log + inbox

  function renderLog(items) {
    const ol = $("log-list");
    clear(ol);
    if (!items.length) {
      ol.append(el("li", { class: "empty empty--sm" }, "Nothing logged yet. Soundings will appear here."));
      return;
    }
    for (const c of items) {
      const errand = state.errandsById.get(String(c.errand_id));
      const kind = errand && errand.spec ? errand.spec.kind : null;
      const name = c.errand_name || c.name || (errand && errand.name) || "—";
      const showMoney = PRICE_KINDS.includes(kind) || (!kind && c.value != null);
      const status = c.status || "";
      ol.append(el("li", null,
        el("span", { class: "t", title: c.at || "", text: hhmm(c.at) }),
        el("span", { class: "n", title: name, text: name }),
        el("span", { class: "s status status--" + status },
          status,
          c.rendered ? el("span", { class: "flag", title: "rendered with JavaScript", text: "JS" }) : null,
          c.llm_used ? el("span", { class: "flag", title: "used the language model", text: "LLM" }) : null),
        el("span", { class: "v", text: showMoney ? money(c.value) : "" }),
        c.message && (status === "error" || status === "triggered" || status === "changed" || /heal/i.test(c.message))
          ? el("span", { class: "m", text: c.message }) : null));
    }
  }

  function renderInbox(items) {
    const ul = $("inbox-list");
    // keep keyboard focus across the 5 s redraw (otherwise it falls back to <body>)
    const ae = document.activeElement;
    const focusKey = ae && ul.contains(ae) && ae.dataset ? ae.dataset.fk : null;
    clear(ul);
    const unread = items.filter((n) => !n.read).length;
    $("inbox-count").textContent = items.length ? unread + " unread" : "";
    if (!items.length) {
      ul.append(el("li", { class: "empty empty--sm" }, "No signals yet. You will hear from Vigil only when a condition is met."));
      return;
    }
    for (const n of items) {
      const id = String(n.id);
      const li = el("li", { class: "note" + (n.read ? "" : " is-unread") },
        el("div", { class: "note__head" },
          el("span", { class: "note__t", title: n.at || "", text: hhmm(n.at) }),
          el("h4", { class: "note__title", text: n.title || "Signal" })),
        n.body ? el("p", { class: "note__body", text: n.body }) : null);
      const foot = el("div", { class: "note__foot" });
      if (!n.read) {
        foot.append(el("button", {
          type: "button", class: "btn btn--sm", dataset: { read: id, fk: "read:" + id },
          "aria-label": "Mark read — " + (n.title || "signal"),
        }, "Mark read"));
      }
      if (n.delivered_to) foot.append(el("span", { class: "note__via", text: "via " + n.delivered_to }));
      if (foot.childNodes.length) li.append(foot);
      ul.append(li);
    }
    if (focusKey) {
      const target = ul.querySelector('[data-fk="' + CSS.escape(focusKey) + '"]');
      if (target) target.focus({ preventScroll: true });
    }
  }

  async function onInboxClick(ev) {
    const b = ev.target.closest("button[data-read]");
    if (!b) return;
    b.disabled = true;
    let failure = null;
    try {
      await api("POST", "/api/inbox/" + encodeURIComponent(b.dataset.read) + "/read");
    } catch (err) {
      failure = err;
    }
    await refresh();
    if (failure) showMsg($("inbox-msg"), "err", "ERR", "Could not mark read: " + failure.message);
    $("h-inbox").setAttribute("tabindex", "-1");
    if (!document.activeElement || document.activeElement === document.body) $("h-inbox").focus();
  }

  // ------------------------------------------------------------------ polling

  let refreshing = null;

  function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const [sys, errs, log, inbox] = await Promise.allSettled([
        api("GET", "/api/system"),
        api("GET", "/api/errands"),
        api("GET", "/api/log?limit=100"),
        api("GET", "/api/inbox?limit=50"),
        loadHistory(),
      ]);

      const allDown = [sys, errs, log, inbox].every((r) => r.status === "rejected");
      state.online = !allDown;

      if (sys.status === "fulfilled") state.system = sys.value || null;
      renderSystem();

      if (errs.status === "fulfilled") {
        state.errands = asList(errs.value);
        state.errandsById = new Map(state.errands.map((e) => [String(e.id), e]));
        if (state.expanded && !state.errandsById.has(state.expanded)) { state.expanded = null; state.history = []; }
        const wm = $("watch-msg");
        const stale = wm.querySelector(".line--err[data-poll]");
        if (stale) clear(wm);
        renderWatches();
      } else {
        const wm = $("watch-msg");
        clear(wm);
        const l = line("err", "ERR", (allDown ? "Lost contact with the API" : "Could not read the watches") + " (" + errs.reason.message + "). Retrying every 5 s.");
        l.dataset.poll = "1";
        wm.append(l);
        if (!state.errands.length) clear($("watch-list"));
      }

      if (log.status === "fulfilled") { clear($("log-msg")); renderLog(asList(log.value)); }
      else showMsg($("log-msg"), "err", "ERR", "Log unavailable: " + log.reason.message);

      if (inbox.status === "fulfilled") {
        clear($("inbox-msg"));
        renderInbox(asList(inbox.value));
      } else showMsg($("inbox-msg"), "err", "ERR", "Inbox unavailable: " + inbox.reason.message);
    })().finally(() => { refreshing = null; });
    return refreshing;
  }

  function schedule() {
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(async () => {
      if (!document.hidden) await refresh();
      schedule();
    }, POLL_MS);
  }

  // ------------------------------------------------------------------ boot

  function boot() {
    tickClock();
    setInterval(tickClock, 15000);
    fillEverySelect(60);

    $("plan-form").addEventListener("submit", onPlan);
    $("prompt").addEventListener("keydown", (ev) => {
      if (ev.key === "Enter" && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); $("plan-form").requestSubmit(); }
    });
    $("spec-form").addEventListener("submit", onKeep);
    $("f-kind").addEventListener("change", syncKindRows);
    $("btn-dry").addEventListener("click", onDryRun);
    $("btn-discard").addEventListener("click", () => { discardSpec(); $("prompt").focus(); });
    $("watch-list").addEventListener("click", onWatchAction);
    $("inbox-list").addEventListener("click", onInboxClick);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });

    refresh().finally(schedule);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
