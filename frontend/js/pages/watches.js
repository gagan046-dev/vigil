/* Watches: searchable, filterable, server-paginated table of errands. */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { conditionText, everyLabel, hostOf } from "../format.js";
import { errorBanner, emptyState, skeletonRows, timeEl, fullRow } from "../components/feedback.js";
import { pager } from "../components/pager.js";
import { menuButton } from "../components/menu.js";
import { errandChip, valueText, delta } from "../components/status.js";
import { openNewWatch } from "../components/new-watch.js";
import { hashQuery, setHashQuery, intParam } from "../store.js";
import { errandMenuItems, poll, debounce } from "./common.js";

const FILTERS = [["", "All"], ["active", "Active"], ["paused", "Paused"], ["failing", "Failing"]];
const COLS = 8;

export function render(main, _params, ctx) {
  const q0 = hashQuery();
  const st = {
    page: intParam(q0.page, 1),
    per: [10, 25, 50].includes(Number(q0.per_page)) ? Number(q0.per_page) : 25,
    q: q0.q || "",
    status: FILTERS.some(([k]) => k === q0.status) ? q0.status : "",
    data: null,
    loading: true,
    error: null,
  };

  const search = el("input", { class: "input", type: "search", placeholder: "Search watches", "aria-label": "Search watches", value: st.q, "data-search": "" });
  const seg = el("div", { class: "segmented", role: "group", "aria-label": "Filter by status" });
  const tbody = el("tbody");
  const pagerBox = el("div");
  const bannerBox = el("div");
  const hint = el("span", { class: "toolbar__hint" }, "Press ", el("kbd", { text: "/" }), " to search, ", el("kbd", { text: "N" }), " for a new watch");

  const table = el("table", { class: "table table--watches" },
    el("thead", null, el("tr", null,
      el("th", { scope: "col", text: "Name" }),
      el("th", { scope: "col", class: "col-md", text: "Condition" }),
      el("th", { scope: "col", class: "num", text: "Last value" }),
      el("th", { scope: "col", class: "num col-lg", text: "Change" }),
      el("th", { scope: "col", class: "col-md", text: "Last check" }),
      el("th", { scope: "col", class: "col-md", text: "Status" }),
      el("th", { scope: "col", class: "col-lg", text: "Schedule" }),
      el("th", { scope: "col", class: "shrink" }, el("span", { class: "visually-hidden", text: "Actions" })))),
    tbody);

  main.append(bannerBox, el("section", { class: "panel", "aria-label": "Watches" },
    el("div", { class: "toolbar" }, el("div", { class: "search" }, icon("search"), search), seg, el("span", { class: "toolbar__spacer" }), hint),
    el("div", { class: "table-wrap" }, table),
    pagerBox));

  function paintSeg() {
    clear(seg);
    for (const [k, label] of FILTERS) {
      const b = el("button", { type: "button", class: "segmented__btn", "aria-pressed": String(st.status === k), text: label });
      b.addEventListener("click", () => { st.status = k; st.page = 1; paintSeg(); load(); });
      seg.append(b);
    }
  }

  function syncUrl() { setHashQuery({ page: st.page, per_page: st.per, q: st.q, status: st.status }, { page: 1, per_page: 25 }); }

  async function load(silent) {
    syncUrl();
    if (!silent) { st.loading = true; paint(); }
    const want = { page: st.page, per_page: st.per, q: st.q, status: st.status };
    try {
      const data = await api.errands(want);
      if (!ctx.alive()) return;
      if (want.page !== st.page || want.q !== st.q || want.status !== st.status || want.per_page !== st.per) return; // stale
      if (data.page > data.pages && data.total > 0) { st.page = data.pages; return load(); }
      st.data = data; st.error = null;
    } catch (e) {
      if (!ctx.alive()) return;
      st.error = e;
    }
    st.loading = false;
    paint();
  }

  function paint() {
    clear(bannerBox);
    if (st.error && st.data) bannerBox.append(errorBanner(st.error, () => load()));
    clear(tbody); clear(pagerBox);
    if (st.loading && !st.data) { tbody.append(...skeletonRows(["70%", "60%", "50%", "40%", "50%", "60%", "50%", "20px"].slice(0, COLS))); return; }
    if (st.error && !st.data) { tbody.append(fullRow(COLS, el("div", { class: "pad" }, errorBanner(st.error, () => load())))); return; }
    const d = st.data;
    if (!d.items.length) {
      const filtered = st.q || st.status;
      tbody.append(fullRow(COLS, filtered
        ? emptyState({ title: "No watches match", text: "Nothing matches the current search or filter.",
            action: el("button", { type: "button", class: "btn", text: "Clear filters", on: { click: () => { st.q = ""; search.value = ""; st.status = ""; st.page = 1; paintSeg(); load(); } } }) })
        : emptyState({ title: "No watches yet", text: "Describe a page and a condition in plain English, for example a price target or a phrase like “back in stock”. Vigil checks it on a schedule and tells you only when it happens.",
            action: el("div", { class: "empty__actions" },
              el("button", { type: "button", class: "btn btn--primary", on: { click: () => openNewWatch() } }, icon("plus"), "New watch"),
              el("a", { class: "btn", href: "#/compare" }, icon("compare"), "Compare prices instead")) })));
      return;
    }
    for (const e of d.items) tbody.append(row(e));
    pagerBox.append(pager(d, (p, per) => { st.page = p; st.per = per; load(); }, { noun: "watches" }));
  }

  function row(e) {
    const spec = e.spec || {};
    const href = "#/watches/" + encodeURIComponent(e.id);
    const target = spec.kind === "best_price_below" ? (spec.query || hostOf(spec.url)) : hostOf(spec.url);
    return el("tr", null,
      el("td", { class: "cell-name" },
        el("a", { class: "cell-main", href, text: e.name || spec.name || "Untitled" }),
        el("span", { class: "cell-sub", text: target }),
        el("span", { class: "cell-sub only-narrow", text: conditionText(spec) || e.condition }),
        el("div", { class: "only-narrow cell-chip" }, errandChip(e))),
      el("td", { class: "col-md" }, el("span", { class: "cell-clip", text: conditionText(spec) || e.condition })),
      el("td", { class: "num value" }, valueText(e.last_value, spec.kind)),
      el("td", { class: "num col-lg" }, delta(e.last_value, e.previous_value ?? e.prev_value)),
      el("td", { class: "col-md nowrap muted" }, timeEl(e.last_checked_at)),
      el("td", { class: "nowrap col-md" }, errandChip(e)),
      el("td", { class: "col-lg nowrap muted", text: everyLabel(spec.every_minutes) }),
      el("td", { class: "shrink" }, menuButton("Actions for " + (e.name || "watch"), () => errandMenuItems(e, { onDone: () => load(true) }))));
  }

  search.addEventListener("input", debounce(() => { st.q = search.value.trim(); st.page = 1; load(); }, 250));
  search.addEventListener("keydown", (e) => { if (e.key === "Escape" && search.value) { search.value = ""; st.q = ""; st.page = 1; load(); } });
  const onChanged = () => load(true);
  window.addEventListener("vigil:watches", onChanged);

  paintSeg();
  load();
  const stop = poll(() => load(true), 15000);
  return () => { stop(); window.removeEventListener("vigil:watches", onChanged); };
}
