/* "New watch" drawer: 1 Describe -> 2 Review -> 3 Test -> 4 Save. */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { money, everyLabel, conditionText, isNum, KIND_LABELS, hostOf } from "../format.js";
import { toast, banner, errorBanner } from "./feedback.js";
import { allowedIntervals, getSystem, watchesChanged } from "../store.js";

const STEPS = ["Describe", "Review", "Test", "Save"];
const DRY_LIMIT_S = 60;

const EXAMPLES = [
  "Tell me when the Aurora 15 laptop at http://vigil-demo-shop:8000/static drops below ₹1,20,000, check every 30 minutes",
  "Alert me when “Out of stock” appears on http://vigil-demo-shop:8000/, hourly",
  "Watch the best price for Aurora 15 across sellers and alert me below ₹1,25,000, every 2 hours",
  "Let me know when http://vigil-demo-shop:8000/notices changes, check daily",
];

let open = null;

export function openNewWatch(prefill) {
  if (open) { open.focus(); return; }
  const st = {
    step: 0,
    prompt: (prefill && prefill.prompt) || "",
    spec: (prefill && prefill.spec) || null,
    source: null,
    notes: [],
    dry: null,
    dryErr: null,
    dryRunning: false,
  };

  const titleId = "nw-title";
  const closeBtn = el("button", { type: "button", class: "icon-btn", "aria-label": "Close" }, icon("x"));
  const stepper = el("ol", { class: "stepper", "aria-label": "Steps" });
  const body = el("div", { class: "drawer__body" });
  const foot = el("div", { class: "drawer__foot" });
  const dlg = el("dialog", { class: "drawer", "aria-labelledby": titleId },
    el("div", { class: "drawer__head" }, el("h2", { class: "drawer__title", id: titleId, text: "New watch" }), closeBtn),
    stepper, body, foot);

  let dryTimer = null;
  const close = () => dlg.close();
  closeBtn.addEventListener("click", close);
  dlg.addEventListener("close", () => { clearInterval(dryTimer); dlg.remove(); open = null; });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  document.body.append(dlg);
  dlg.showModal();
  open = dlg;
  getSystem().catch(() => null).then(render);

  function renderStepper() {
    clear(stepper);
    STEPS.forEach((name, i) => {
      stepper.append(el("li", { class: "stepper__item", "data-state": i < st.step ? "done" : i === st.step ? "current" : "todo", "aria-current": i === st.step ? "step" : null },
        el("span", { class: "stepper__num", text: i < st.step ? "" : String(i + 1) }, i < st.step ? icon("check", "icon--xs") : null), name));
    });
  }

  function render() {
    renderStepper();
    clear(body); clear(foot);
    [stepDescribe, stepReview, stepTest, stepSave][st.step]();
  }

  // ------------------------------------------------------------ 1 Describe
  function stepDescribe() {
    const ta = el("textarea", { class: "textarea", id: "nw-prompt", rows: 5, maxlength: 2000, spellcheck: "true",
      placeholder: "Tell me when the laptop at https://… drops below ₹1,20,000, check every 30 minutes" });
    ta.value = st.prompt;
    const err = el("div");
    body.append(
      el("div", { class: "field" },
        el("label", { class: "field__label", for: "nw-prompt", text: "What should Vigil watch?" }),
        ta,
        el("p", { class: "field__hint", text: "Name a page (or a product for best-price watches), a condition and how often. Quote exact words to look for." })),
      err,
      el("div", { class: "nw-examples" },
        el("p", { class: "nw-examples__label", text: "Examples" }),
        el("ul", { role: "list", class: "nw-examples__list" },
          EXAMPLES.map((x) => el("li", null, el("button", { type: "button", class: "nw-example", text: x, on: { click: () => { ta.value = x; ta.focus(); } } }))))));
    const manual = el("button", { type: "button", class: "btn btn--ghost", text: "Fill in the form instead" });
    const next = el("button", { type: "button", class: "btn btn--primary" }, "Continue", icon("chev-right"));
    foot.append(manual, el("span", { class: "spacer" }), el("button", { type: "button", class: "btn", text: "Cancel", on: { click: close } }), next);
    manual.addEventListener("click", () => {
      st.prompt = ta.value.trim();
      st.spec = st.spec || { name: "", kind: "price_below", url: "", threshold: null, keyword: null, selector: null, render: false, every_minutes: 60 };
      st.source = "manual"; st.notes = [];
      st.step = 1; render();
    });
    const go = async () => {
      const text = ta.value.trim();
      if (text.length < 8) { mount(err, banner("warn", "Describe the watch in a sentence first.")); ta.focus(); return; }
      st.prompt = text;
      next.disabled = true;
      mount(next, el("span", { class: "spinner", "aria-hidden": "true" }), "Planning…");
      try {
        const r = await api.plan(text);
        st.spec = r.spec; st.source = r.source; st.notes = r.notes || [];
        st.dry = null; st.dryErr = null;
        st.step = 1; render();
      } catch (e) {
        next.disabled = false;
        mount(next, "Continue", icon("chev-right"));
        mount(err, errorBanner(e));
      }
    };
    next.addEventListener("click", go);
    ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) go(); });
    setTimeout(() => ta.focus(), 0);
  }

  // ------------------------------------------------------------ 2 Review
  let form = null;
  function stepReview() {
    const s = st.spec;
    const intervals = allowedIntervals();
    const f = {
      name: el("input", { class: "input", id: "f-name", maxlength: 80, value: s.name || "" }),
      kind: el("select", { class: "select", id: "f-kind" }, Object.entries(KIND_LABELS).map(([k, v]) => el("option", { value: k, text: v, selected: s.kind === k }))),
      url: el("input", { class: "input input--mono", id: "f-url", type: "url", inputmode: "url", value: s.url || "", placeholder: "https://" }),
      query: el("input", { class: "input", id: "f-query", maxlength: 200, value: s.query || "", placeholder: "e.g. Aurora 15 RTX 5070 16GB" }),
      threshold: el("input", { class: "input", id: "f-threshold", inputmode: "decimal", value: isNum(s.threshold) ? String(s.threshold) : "", placeholder: "120000" }),
      keyword: el("input", { class: "input", id: "f-keyword", maxlength: 120, value: s.keyword || "" }),
      selector: el("input", { class: "input input--mono", id: "f-selector", maxlength: 300, value: s.selector || "", placeholder: "optional, e.g. .price-now" }),
      render: el("input", { type: "checkbox", id: "f-render", checked: !!s.render }),
      every: el("select", { class: "select", id: "f-every" }, intervals.map((n) => el("option", { value: String(n), text: everyLabel(n), selected: Number(s.every_minutes) === n }))),
    };
    if (!intervals.includes(Number(s.every_minutes))) f.every.value = String(intervals.includes(60) ? 60 : intervals[0]);
    const errs = {};
    const field = (key, label, input, hint, wide) => {
      errs[key] = el("p", { class: "field__error", id: "e-" + key, hidden: true });
      input.setAttribute("aria-describedby", "e-" + key);
      return el("div", { class: "field" + (wide ? " field--wide" : ""), dataset: { f: key } },
        el("label", { class: "field__label", for: input.id, text: label }), input,
        hint ? el("p", { class: "field__hint", text: hint }) : null, errs[key]);
    };
    const thrHint = el("p", { class: "field__hint" });
    const grid = el("div", { class: "form-grid" },
      field("name", "Name", f.name, null, true),
      field("kind", "Condition", f.kind),
      field("every", "Check", f.every),
      field("url", "Page URL", f.url, null, true),
      field("query", "Product", f.query, "Searched across the configured sellers on every check.", true),
      field("threshold", "Target price (₹)", f.threshold),
      field("keyword", "Exact text", f.keyword),
      field("selector", "CSS selector", f.selector, "Leave empty and Vigil finds the value and learns a selector.", true),
      el("div", { class: "field field--wide", dataset: { f: "render" } },
        el("label", { class: "checkbox", for: "f-render" }, f.render, "Run the page’s JavaScript (slower; for prices drawn by scripts)")));
    grid.querySelector('[data-f="threshold"]').append(thrHint);

    const sync = () => {
      const k = f.kind.value;
      const show = (key, on) => { grid.querySelector('[data-f="' + key + '"]').hidden = !on; };
      show("threshold", k === "price_below" || k === "price_above" || k === "best_price_below");
      show("keyword", k === "text_appears" || k === "text_disappears");
      show("query", k === "best_price_below");
      show("selector", k !== "best_price_below");
      show("render", k !== "best_price_below");
      grid.querySelector('[data-f="url"] .field__label').textContent = k === "best_price_below" ? "Product URL (optional)" : "Page URL";
      thrHint.textContent = k === "best_price_below" ? "Optional. Without a target you are told whenever the best price drops." : "";
      thrHint.hidden = k !== "best_price_below";
    };
    f.kind.addEventListener("change", sync);
    sync();

    const notes = st.notes && st.notes.length
      ? el("ul", { class: "notes", "aria-label": "Planner notes" }, st.notes.map((n) => el("li", null, icon("info"), el("span", { text: n }))))
      : null;
    const sourceText = st.source === "manual" ? "Entered by hand" : st.source === "llm" ? "Planned by the language model" : st.source ? "Planned by rules" : "";
    body.append(
      el("div", { class: "nw-review-head" },
        el("p", { class: "nw-prompt-echo", text: st.prompt || "No description" }),
        sourceText ? el("span", { class: "tag", text: sourceText }) : null),
      grid,
      notes ? el("div", { class: "nw-notes" }, el("p", { class: "field__label", text: "Planner notes" }), notes) : null);

    form = { f, errs, validate };
    function validate() {
      let ok = true;
      const setErr = (k, msg) => {
        errs[k].textContent = msg || ""; errs[k].hidden = !msg;
        if (f[k] && f[k].setAttribute) f[k].setAttribute("aria-invalid", msg ? "true" : "false");
        if (msg) ok = false;
      };
      const k = f.kind.value;
      const name = f.name.value.trim();
      const url = f.url.value.trim();
      const q = f.query.value.trim();
      const thrRaw = f.threshold.value.replace(/[,\s₹]/g, "");
      setErr("name", name ? "" : "Give the watch a short name.");
      let urlOk = false;
      try { const u = new URL(url); urlOk = (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname; } catch (_) { urlOk = false; }
      if (k === "best_price_below") {
        setErr("url", url && !urlOk ? "Enter a full http(s) URL, or leave it empty." : "");
        setErr("query", q || url ? "" : "Enter the product name to search for.");
      } else {
        setErr("url", urlOk ? "" : "Enter a full http(s) URL, e.g. https://shop.example/item.");
        setErr("query", "");
      }
      const thr = thrRaw === "" ? null : Number(thrRaw);
      if (k === "price_below" || k === "price_above") setErr("threshold", thr && thr > 0 ? "" : "Enter a target price greater than zero.");
      else if (k === "best_price_below") setErr("threshold", thr === null || thr > 0 ? "" : "Enter a positive number or leave it empty.");
      else setErr("threshold", "");
      if (k === "text_appears" || k === "text_disappears") setErr("keyword", f.keyword.value.trim() ? "" : "Enter the exact text to look for.");
      else setErr("keyword", "");
      if (!ok) { const first = body.querySelector('[aria-invalid="true"]'); if (first) first.focus(); return null; }
      const spec = {
        name, kind: k,
        url: url || null,
        threshold: ["price_below", "price_above", "best_price_below"].includes(k) ? thr : null,
        keyword: k === "text_appears" || k === "text_disappears" ? f.keyword.value.trim() : null,
        selector: k === "best_price_below" ? null : (f.selector.value.trim() || null),
        render: k === "best_price_below" ? false : f.render.checked,
        every_minutes: Number(f.every.value),
      };
      if (k === "best_price_below") spec.query = q || null;
      return spec;
    }

    const back = el("button", { type: "button", class: "btn", text: "Back", on: { click: () => { st.step = 0; render(); } } });
    const skip = el("button", { type: "button", class: "btn btn--ghost", text: "Skip test" });
    const test = el("button", { type: "button", class: "btn btn--primary" }, icon("flask"), "Test it");
    skip.addEventListener("click", () => { const spec = validate(); if (spec) { st.spec = spec; st.dry = null; st.step = 3; render(); } });
    test.addEventListener("click", () => { const spec = validate(); if (spec) { st.spec = spec; st.step = 2; render(); runDry(); } });
    foot.append(back, el("span", { class: "spacer" }), skip, test);
    setTimeout(() => f.name.focus(), 0);
  }

  // ------------------------------------------------------------ 3 Test
  const dryBox = el("div", { class: "nw-dry" });
  function stepTest() {
    body.append(
      el("p", { class: "nw-step-lead", text: "A worker fetches the page once, exactly as a scheduled check would, without saving anything." }),
      specSummary(st.spec), dryBox);
    paintDry();
    const back = el("button", { type: "button", class: "btn", text: "Back", on: { click: () => { st.step = 1; render(); } } });
    const again = el("button", { type: "button", class: "btn", disabled: st.dryRunning }, icon("refresh"), "Run again");
    again.addEventListener("click", runDry);
    const next = el("button", { type: "button", class: "btn btn--primary", disabled: st.dryRunning }, "Continue", icon("chev-right"));
    next.addEventListener("click", () => { st.step = 3; render(); });
    foot.append(back, el("span", { class: "spacer" }), again, next);
  }

  function paintDry() {
    clear(dryBox);
    if (st.dryRunning) {
      const bar = el("div", { class: "progress__bar" });
      const secs = el("span", { class: "muted", text: "0 s" });
      dryBox.append(el("div", { class: "nw-dry__wait", role: "status" },
        el("div", { class: "nw-dry__row" }, el("span", { class: "spinner", "aria-hidden": "true" }), el("span", { text: "Fetching the page…" }), el("span", { class: "spacer" }), secs),
        el("div", { class: "progress" }, bar),
        el("p", { class: "field__hint", text: "Pages that need JavaScript take longer. Gives up after " + DRY_LIMIT_S + " s." })));
      const started = st.dryStarted;
      clearInterval(dryTimer);
      const tick = () => {
        const s = Math.min(DRY_LIMIT_S, (Date.now() - started) / 1000);
        secs.textContent = Math.round(s) + " s";
        bar.style.width = Math.min(98, (s / DRY_LIMIT_S) * 100) + "%";
      };
      tick();
      dryTimer = setInterval(tick, 500);
      return;
    }
    clearInterval(dryTimer);
    if (st.dryErr) { dryBox.append(errorBanner(st.dryErr, runDry)); return; }
    const r = st.dry;
    if (!r) return;
    if (!r.ok) {
      dryBox.append(el("div", { class: "result result--bad" },
        el("p", { class: "result__title" }, icon("alert"), "The test check failed"),
        el("p", { class: "result__msg", text: r.message || "No details." }),
        el("p", { class: "field__hint", text: "Go back to adjust the URL or selector, or save anyway and let the schedule retry." })));
      return;
    }
    const isPrice = ["price_below", "price_above", "best_price_below"].includes(st.spec.kind);
    const met = r.condition_true === true;
    dryBox.append(el("div", { class: "result" + (met ? " result--met" : "") },
      el("p", { class: "result__title" }, icon("check"), "Test check succeeded"),
      el("dl", { class: "facts" },
        isPrice ? [el("dt", { text: "Value found" }), el("dd", { class: "result__value", text: money(r.value) })] : null,
        el("dt", { text: "Condition now" }), el("dd", { text: r.condition_true === null || r.condition_true === undefined ? "Not applicable" : met ? "Met — you would be alerted" : "Not met" }),
        el("dt", { text: "Rendered" }), el("dd", { text: r.rendered ? "Yes, with JavaScript" : "No, plain HTML" }),
        r.selector ? [el("dt", { text: "Selector" }), el("dd", { class: "url", text: r.selector })] : null),
      r.message ? el("p", { class: "result__msg", text: r.message }) : null,
      r.excerpt ? el("blockquote", { class: "result__excerpt", text: r.excerpt }) : null));
  }

  async function runDry() {
    st.dryRunning = true; st.dryErr = null; st.dry = null; st.dryStarted = Date.now();
    if (st.step === 2) render();
    try {
      st.dry = await api.dryrun(st.spec);
    } catch (e) {
      st.dryErr = e;
    } finally {
      st.dryRunning = false;
      if (open && st.step === 2) render();
    }
  }

  // ------------------------------------------------------------ 4 Save
  function stepSave() {
    const err = el("div");
    body.append(el("p", { class: "nw-step-lead", text: "Vigil checks on this schedule and writes to your inbox only when the condition becomes true." }),
      specSummary(st.spec), err);
    const back = el("button", { type: "button", class: "btn", text: "Back", on: { click: () => { st.step = st.dry ? 2 : 1; render(); } } });
    const save = el("button", { type: "button", class: "btn btn--primary" }, icon("check"), "Save watch");
    save.addEventListener("click", async () => {
      save.disabled = true;
      mount(save, el("span", { class: "spinner", "aria-hidden": "true" }), "Saving…");
      try {
        const created = await api.createErrand(st.spec, st.prompt);
        close();
        toast("Watch saved. The first check is queued.");
        watchesChanged();
        if (created && created.id) location.hash = "#/watches/" + encodeURIComponent(created.id);
      } catch (e) {
        save.disabled = false;
        mount(save, icon("check"), "Save watch");
        mount(err, errorBanner(e));
      }
    });
    foot.append(back, el("span", { class: "spacer" }), save);
    setTimeout(() => save.focus(), 0);
  }

  function specSummary(s) {
    return el("dl", { class: "facts nw-summary" },
      el("dt", { text: "Name" }), el("dd", { text: s.name }),
      el("dt", { text: "Condition" }), el("dd", { text: conditionText(s) }),
      s.query ? [el("dt", { text: "Product" }), el("dd", { text: s.query })] : null,
      s.url ? [el("dt", { text: "URL" }), el("dd", { class: "url", text: s.url, title: hostOf(s.url) })] : null,
      s.selector ? [el("dt", { text: "Selector" }), el("dd", { class: "url", text: s.selector })] : null,
      el("dt", { text: "Schedule" }), el("dd", { text: everyLabel(s.every_minutes) + (s.render ? " · renders JavaScript" : "") }));
  }
}
