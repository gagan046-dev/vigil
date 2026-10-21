/* One comparison: live progress, best-price summary, offers table, "Track best price". */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { money, hostOf, isNum, exact, relative, everyLabel, plural, parseTime } from "../format.js";
import { errorBanner, emptyState, skeletonBlock, toast, banner } from "../components/feedback.js";
import { attachTip } from "../components/menu.js";
import { allowedIntervals, getSystem, watchesChanged } from "../store.js";

const ACTIVE = ["queued", "searching", "fetching"];
const PHASES = [["queued", "Queued"], ["searching", "Searching sellers"], ["fetching", "Reading prices"], ["done", "Done"]];

export function runStatusChip(status) {
  const map = {
    queued: ["chip--muted", "Queued"],
    searching: ["chip--active", "Searching"],
    fetching: ["chip--active", "Reading prices"],
    done: ["chip--ok chip--plain", "Done"],
    error: ["chip--danger", "Failed"],
  };
  const [cls, label] = map[status] || ["chip--muted", status || "—"];
  return el("span", { class: "chip " + cls, text: label });
}

function offerKey(o) { return (o && (o.url || "")) + "|" + (o && (o.seller || "")); }
function sellerName(o) { return o.seller_name || o.seller || hostOf(o.url) || "Unknown seller"; }
function noteList(r) { const n = r && r.notes; return Array.isArray(n) ? n : n && Array.isArray(n.messages) ? n.messages : []; }
function hasPrice(o) { return !o.error && isNum(o.price); }
function matchLevel(o) {
  const s = Number(o.match_score);
  if (!isNum(o.match_score)) return null;
  if (o.match === "possible" || o.possible_match || (s >= 0.5 && s < 0.75)) return "possible";
  return s >= 0.75 ? "match" : "low";
}

export function render(main, [id], ctx) {
  const st = { run: null, err: null, startedPoll: Date.now() };
  ctx.setTitle("Comparison", [{ label: "Compare", href: "#/compare" }]);

  const head = el("div", { class: "run-head" }, skeletonBlock(2));
  const progress = el("div");
  const summary = el("div");
  const offersBox = el("section", { class: "panel", "aria-labelledby": "h-offers" });
  const side = el("aside", { class: "detail-side" });
  main.append(head, progress, el("div", { class: "detail-grid" }, el("div", { class: "detail-main" }, summary, offersBox), side));

  let timer = null;
  async function load() {
    try {
      const r = await api.compareRun(id);
      if (!ctx.alive()) return;
      st.run = r; st.err = null;
    } catch (e) {
      if (!ctx.alive()) return;
      st.err = e;
    }
    paint();
    clearTimeout(timer);
    if (st.run && ACTIVE.includes(st.run.status)) timer = setTimeout(load, 1500);
    else if (!st.run && st.err && st.err.status !== 404) timer = setTimeout(load, 5000);
  }

  function paint() {
    if (!st.run) {
      clear(head); clear(progress); clear(summary); clear(offersBox); clear(side);
      if (st.err && st.err.status === 404) {
        ctx.setTitle("Not found", [{ label: "Compare", href: "#/compare" }]);
        head.append(emptyState({ title: "This comparison no longer exists", action: el("a", { class: "btn", href: "#/compare", text: "New comparison" }) }));
      } else if (st.err) head.append(errorBanner(st.err, load));
      return;
    }
    const r = st.run;
    const title = (r.product && r.product.title) || r.query;
    ctx.setTitle(title, [{ label: "Compare", href: "#/compare" }]);
    paintHead(r, title);
    paintProgress(r);
    paintSummary(r);
    paintOffers(r);
    paintSide(r);
  }

  function paintHead(r, title) {
    clear(head);
    const again = el("button", { type: "button", class: "btn" }, icon("refresh"), "Run again");
    again.disabled = ACTIVE.includes(r.status);
    again.addEventListener("click", async () => {
      again.disabled = true;
      try { const n = await api.compare(r.query); location.hash = "#/compare/" + encodeURIComponent(n.id); }
      catch (e) { again.disabled = false; toast(e.message, { error: true }); }
    });
    head.append(
      el("div", { class: "detail-head__main" },
        el("div", { class: "detail-head__cond" }, runStatusChip(r.status)),
        el("p", { class: "detail-head__meta" },
          title !== r.query ? ["Searched for “", r.query, "” · "] : null,
          "Started ", el("time", { datetime: r.created_at || "", title: exact(r.created_at), text: relative(r.created_at) }),
          r.finished_at ? [" · took ", secondsBetween(r.created_at, r.finished_at)] : null)),
      el("div", { class: "detail-head__actions" }, again));
  }

  function paintProgress(r) {
    clear(progress);
    if (!ACTIVE.includes(r.status)) return;
    const idx = PHASES.findIndex(([k]) => k === r.status);
    const n = r.notes || {};
    const fetched = Number(n.fetched), total = Number(n.total);
    const elapsed = Math.max(0, Math.round((Date.now() - (parseTime(r.created_at) || new Date(st.startedPoll)).getTime()) / 1000));
    progress.append(el("section", { class: "panel run-progress", role: "status", "aria-live": "polite" },
      el("ol", { class: "phases", role: "list" },
        PHASES.map(([k, label], i) => el("li", { class: "phase", "data-state": i < idx ? "done" : i === idx ? "current" : "todo" },
          el("span", { class: "phase__mark" }, i < idx ? icon("check", "icon--xs") : i === idx ? el("span", { class: "spinner", "aria-hidden": "true" }) : null),
          el("span", { text: label })))),
      el("p", { class: "run-progress__note" },
        r.status === "fetching" && total > 0 && fetched >= 0 ? "Read " + fetched + " of " + plural(total, "seller page") + ". " : "",
        r.status === "searching" ? "Looking up the product at each seller. " : "",
        el("span", { class: "muted", text: elapsed + " s elapsed" }))));
  }

  function paintSummary(r) {
    clear(summary);
    if (r.status === "error") {
      summary.append(el("div", { class: "banner", role: "alert" }, icon("alert"),
        el("div", { class: "banner__msg", text: (r.error || noteList(r)[0] || "The comparison failed.") })));
      return;
    }
    const offers = Array.isArray(r.offers) ? r.offers : [];
    const priced = offers.filter(hasPrice);
    const best = r.best || null;
    if (!best) {
      if (r.status === "done") summary.append(banner("warn", priced.length ? "No listing matched the product closely enough to call a best price." : "No seller returned a price for this product. See the reasons below."));
      return;
    }
    const others = priced.filter((o) => offerKey(o) !== offerKey(best) && matchLevel(o) !== "low").map((o) => Number(o.price));
    const nextCheapest = others.length ? Math.min(...others) : null;
    const highest = others.length ? Math.max(...others) : null;
    const facts = [];
    if (nextCheapest !== null && nextCheapest > best.price) facts.push(["Next cheapest", money(nextCheapest), money(nextCheapest - best.price) + " more"]);
    if (highest !== null && highest > best.price) facts.push(["Highest", money(highest), "you save " + money(highest - best.price)]);
    if (isNum(best.mrp) && Number(best.mrp) > Number(best.price)) facts.push(["MRP", money(best.mrp), Math.round((1 - best.price / best.mrp) * 100) + "% off"]);
    summary.append(el("section", { class: "panel best", "aria-label": "Best price" },
      el("div", { class: "best__main" },
        el("p", { class: "best__label", text: "Best price" }),
        el("p", { class: "best__price", text: money(best.price, best.currency) }),
        el("p", { class: "best__seller" }, "at ", el("strong", { text: sellerName(best) }),
          best.in_stock === true ? el("span", { class: "chip chip--ok chip--plain", text: "In stock" }) : null),
        best.url ? el("a", { class: "btn btn--primary best__go", href: best.url, target: "_blank", rel: "noopener noreferrer" }, "Open at ", sellerName(best), icon("external")) : null),
      el("dl", { class: "best__facts" },
        facts.map(([k, v, s]) => el("div", { class: "best__fact" }, el("dt", { text: k }), el("dd", null, el("span", { class: "best__fact-v", text: v }), el("span", { class: "best__fact-s", text: s })))),
        el("div", { class: "best__fact" }, el("dt", { text: "Compared" }),
          el("dd", null, el("span", { class: "best__fact-v", text: priced.length + " of " + offers.length }), el("span", { class: "best__fact-s", text: "sellers with a price" }))))));
  }

  function paintOffers(r) {
    clear(offersBox);
    const offers = (Array.isArray(r.offers) ? r.offers : []).slice();
    const bestKey = r.best ? offerKey(r.best) : null;
    offersBox.append(el("div", { class: "panel__head" },
      el("h2", { class: "panel__title", id: "h-offers", text: "Offers" }),
      el("span", { class: "panel__sub", text: "In stock first, then by price" })));
    if (!offers.length) {
      offersBox.append(ACTIVE.includes(r.status)
        ? el("div", { class: "panel__body" }, skeletonBlock(4))
        : emptyState({ title: "No seller pages found", text: "The search returned nothing for the configured sellers. Try a shorter product name, or paste a product page URL." }));
      return;
    }
    const rank = (o) => (o.error ? 3 : !isNum(o.price) ? 2 : o.in_stock === false ? 1 : 0);
    offers.sort((a, b) => rank(a) - rank(b) || (Number(a.price) || 0) - (Number(b.price) || 0));
    const tbody = el("tbody");
    for (const o of offers) tbody.append(offerRow(o, offerKey(o) === bestKey));
    offersBox.append(el("div", { class: "table-wrap" }, el("table", { class: "table table--offers" },
      el("thead", null, el("tr", null,
        el("th", { scope: "col", text: "Seller" }),
        el("th", { scope: "col", class: "num", text: "Price" }),
        el("th", { scope: "col", class: "num col-md", text: "MRP" }),
        el("th", { scope: "col", class: "col-md", text: "Stock" }),
        el("th", { scope: "col", class: "num", text: "Match" }),
        el("th", { scope: "col", class: "col-lg", text: "Source" }),
        el("th", { scope: "col", class: "shrink" }, el("span", { class: "visually-hidden", text: "Link" })))),
      tbody)));
  }

  function offerRow(o, isBest) {
    const link = o.url ? el("a", { class: "icon-btn", href: o.url, target: "_blank", rel: "noopener noreferrer", "aria-label": "Open " + sellerName(o) + " in a new tab", title: o.url }, icon("external")) : null;
    if (o.error || !isNum(o.price)) {
      return el("tr", { class: "is-dim" },
        el("td", null, el("span", { class: "cell-main", text: sellerName(o) }), o.title ? el("span", { class: "cell-sub", text: o.title }) : null),
        el("td", { class: "offer-error", colspan: 5 }, icon("alert", "icon--xs"), el("span", { text: o.error || "No price found" }),
          o.match === "rejected" && o.match_reason ? el("span", { class: "muted", text: " · " + o.match_reason }) : null),
        el("td", { class: "shrink" }, link));
    }
    const lvl = matchLevel(o);
    const matchCell = el("span", { class: "match match--" + (lvl || "none"), tabindex: o.match_reason ? "0" : null },
      isNum(o.match_score) ? Math.round(Number(o.match_score) * 100) + "%" : "—",
      lvl === "possible" ? el("span", { class: "tag", text: "Possible" }) : null);
    if (o.match_reason) attachTip(matchCell, o.match_reason);
    const discount = isNum(o.mrp) && Number(o.mrp) > Number(o.price) ? Math.round((1 - o.price / o.mrp) * 100) : null;
    return el("tr", { class: (isBest ? "is-best" : "") + (lvl === "low" ? " is-dim" : "") },
      el("td", null,
        el("span", { class: "cell-main" }, sellerName(o), isBest ? el("span", { class: "chip chip--ok chip--plain best-chip", text: "Best" }) : null),
        o.title ? el("span", { class: "cell-sub", title: o.title, text: o.title }) : null),
      el("td", { class: "num value" }, money(o.price, o.currency)),
      el("td", { class: "num col-md" }, isNum(o.mrp) ? [el("span", { class: "strike", text: money(o.mrp, o.currency) }), discount ? el("span", { class: "discount", text: " −" + discount + "%" }) : null] : el("span", { class: "muted", text: "—" })),
      el("td", { class: "col-md nowrap" }, stockText(o.in_stock)),
      el("td", { class: "num nowrap" }, matchCell),
      el("td", { class: "col-lg" }, o.source ? el("span", { class: "tag", text: sourceLabel(o.source) }) : null),
      el("td", { class: "shrink" }, link));
  }

  function paintSide(r) {
    clear(side);
    const panel = el("section", { class: "panel", "aria-labelledby": "h-track" },
      el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-track", text: "Track best price" })));
    const body = el("div", { class: "panel__body track" });
    panel.append(body);
    side.append(panel);
    if (r.errand_id) {
      body.append(el("p", { class: "muted", text: "This comparison is tracked by a watch." }),
        el("a", { class: "btn", href: "#/watches/" + encodeURIComponent(r.errand_id) }, "Open watch", icon("chev-right")));
    } else if (r.status !== "done" || !r.best) {
      body.append(el("p", { class: "muted", text: r.status === "done" ? "Tracking needs at least one matching offer." : "Available when the comparison finishes." }));
    } else {
      getSystem().catch(() => null).then(() => { if (ctx.alive()) trackForm(body, r); });
    }
    const notes = noteList(r);
    if (notes.length) {
      side.append(el("section", { class: "panel", "aria-labelledby": "h-notes" },
        el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-notes", text: "Notes" })),
        el("div", { class: "panel__body" }, el("ul", { class: "notes" }, notes.map((n) => el("li", null, icon("info"), el("span", { text: String(n) })))))));
    }
  }

  function trackForm(body, r) {
    clear(body);
    const intervals = allowedIntervals().filter((n) => n >= 60);
    const thr = el("input", { class: "input", id: "trk-thr", inputmode: "decimal", placeholder: "Any drop below " + money(r.best.price) });
    const every = el("select", { class: "select", id: "trk-every" }, (intervals.length ? intervals : allowedIntervals()).map((n) => el("option", { value: String(n), text: everyLabel(n), selected: n === 120 })));
    const err = el("p", { class: "field__error", hidden: true, id: "trk-err" });
    thr.setAttribute("aria-describedby", "trk-err");
    const btn = el("button", { type: "submit", class: "btn btn--primary" }, icon("target"), "Track best price");
    const form = el("form", { class: "track__form", novalidate: true },
      el("p", { class: "track__lead", text: "Re-run this comparison on a schedule and get an inbox alert when the best price falls." }),
      el("div", { class: "field" }, el("label", { class: "field__label", for: "trk-thr", text: "Alert below (₹)" }), thr,
        el("p", { class: "field__hint", text: "Leave empty to be told whenever the best price drops." }), err),
      el("div", { class: "field" }, el("label", { class: "field__label", for: "trk-every", text: "Check" }), every,
        el("p", { class: "field__hint", text: "Hourly or slower keeps you polite to real stores." })),
      btn);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const raw = thr.value.replace(/[,\s₹]/g, "");
      const t = raw === "" ? null : Number(raw);
      if (raw !== "" && !(t > 0)) { err.textContent = "Enter a positive amount, or leave it empty."; err.hidden = false; thr.setAttribute("aria-invalid", "true"); thr.focus(); return; }
      err.hidden = true; thr.removeAttribute("aria-invalid");
      btn.disabled = true;
      mount(btn, el("span", { class: "spinner", "aria-hidden": "true" }), "Saving…");
      try {
        const body2 = { every_minutes: Number(every.value) };
        if (t !== null) body2.threshold = t;
        const e = await api.trackCompare(r.id, body2);
        watchesChanged();
        toast("Tracking the best price.", { link: e && e.id ? { href: "#/watches/" + encodeURIComponent(e.id), label: "Open watch" } : null });
        st.run.errand_id = e && e.id;
        paintSide(st.run);
      } catch (e2) {
        btn.disabled = false;
        mount(btn, icon("target"), "Track best price");
        err.textContent = e2.message; err.hidden = false;
      }
    });
    body.append(form);
  }

  load();
  return () => clearTimeout(timer);
}

function stockText(v) {
  if (v === true) return el("span", { class: "stock stock--in", text: "In stock" });
  if (v === false) return el("span", { class: "stock stock--out", text: "Out of stock" });
  return el("span", { class: "muted", text: "Unknown" });
}

function sourceLabel(s) {
  const m = { jsonld: "JSON-LD", "json-ld": "JSON-LD", json_ld: "JSON-LD", meta: "Meta tags", heuristic: "Page text", heuristics: "Page text", regex: "Page text", llm: "LLM", selector: "Selector", render: "Rendered" };
  return m[String(s).toLowerCase()] || String(s);
}

function secondsBetween(a, b) {
  const s = (parseTime(b) - parseTime(a)) / 1000;
  if (!isFinite(s) || s < 0) return "—";
  return s < 60 ? Math.round(s) + " s" : Math.round(s / 60) + " min";
}
