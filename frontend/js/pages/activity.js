/* Activity: every check, newest first. Filter by result and watch; grouped by day; paginated. */
import { el, icon, clear } from "../dom.js";
import { api } from "../api.js";
import { day, clock, exact, duration } from "../format.js";
import { errorBanner, emptyState, skeletonRows, fullRow } from "../components/feedback.js";
import { pager } from "../components/pager.js";
import { checkChip, valueText } from "../components/status.js";
import { hashQuery, setHashQuery, intParam } from "../store.js";
import { poll } from "./common.js";

const STATUSES = [["", "All results"], ["triggered", "Triggered"], ["changed", "Changed"], ["unchanged", "Unchanged"], ["baseline", "Baseline"], ["error", "Errors"]];
const COLS = 7;

export function render(main, _params, ctx) {
  const q0 = hashQuery();
  const st = {
    page: intParam(q0.page, 1),
    per: [25, 50, 100].includes(Number(q0.per_page)) ? Number(q0.per_page) : 50,
    status: STATUSES.some(([k]) => k === q0.status) ? q0.status : "",
    errand: q0.errand_id || "",
    data: null, error: null,
  };

  const statusSel = el("select", { class: "select select--sm", "aria-label": "Result" },
    STATUSES.map(([k, l]) => el("option", { value: k, text: l, selected: st.status === k })));
  const watchSel = el("select", { class: "select select--sm", "aria-label": "Watch" }, el("option", { value: "", text: "All watches" }));
  const live = el("span", { class: "toolbar__hint" }, el("span", { class: "live-dot", "aria-hidden": "true" }), "Updates every 10 s");
  const tbody = el("tbody");
  const pagerBox = el("div");
  const bannerBox = el("div");

  main.append(bannerBox, el("section", { class: "panel", "aria-label": "Activity log" },
    el("div", { class: "toolbar" }, statusSel, watchSel, el("span", { class: "toolbar__spacer" }), live),
    el("div", { class: "table-wrap" }, el("table", { class: "table table--compact table--log" },
      el("thead", null, el("tr", null,
        el("th", { scope: "col", text: "Time" }),
        el("th", { scope: "col", text: "Watch" }),
        el("th", { scope: "col", text: "Result" }),
        el("th", { scope: "col", class: "num col-md", text: "Value" }),
        el("th", { scope: "col", class: "num col-md", text: "Took" }),
        el("th", { scope: "col", class: "col-md", text: "Path" }),
        el("th", { scope: "col", class: "col-lg", text: "Message" }))),
      tbody)),
    pagerBox));

  // Watch filter options (first 100 by name is plenty for a personal tool).
  api.errands({ page: 1, per_page: 100 }).then((d) => {
    if (!ctx.alive()) return;
    const items = d.items.slice().sort((a, b) => String(a.name).localeCompare(String(b.name)));
    for (const e of items) watchSel.append(el("option", { value: e.id, text: e.name, selected: e.id === st.errand }));
    if (st.errand && !items.some((e) => e.id === st.errand)) watchSel.append(el("option", { value: st.errand, text: "Selected watch", selected: true }));
  }).catch(() => null);

  statusSel.addEventListener("change", () => { st.status = statusSel.value; st.page = 1; load(); });
  watchSel.addEventListener("change", () => { st.errand = watchSel.value; st.page = 1; load(); });

  async function load(silent) {
    setHashQuery({ page: st.page, per_page: st.per, status: st.status, errand_id: st.errand }, { page: 1, per_page: 50 });
    if (!silent && !st.data) { clear(tbody); tbody.append(...skeletonRows(["50px", "60%", "60px", "50%", "40px", "40px", "70%"], 10)); }
    try {
      const d = await api.log({ page: st.page, per_page: st.per, status: st.status, errand_id: st.errand });
      if (!ctx.alive()) return;
      if (d.page > d.pages && d.total > 0) { st.page = d.pages; return load(silent); }
      st.data = d; st.error = null;
    } catch (e) { if (!ctx.alive()) return; st.error = e; }
    paint();
  }

  function paint() {
    clear(tbody); clear(pagerBox); clear(bannerBox);
    if (st.error && st.data) bannerBox.append(errorBanner(st.error, () => load()));
    if (st.error && !st.data) { tbody.append(fullRow(COLS, el("div", { class: "pad" }, errorBanner(st.error, () => load())))); return; }
    const d = st.data;
    if (!d.items.length) {
      tbody.append(fullRow(COLS, (st.status || st.errand)
        ? emptyState({ title: "No checks match these filters", action: el("button", { type: "button", class: "btn", text: "Clear filters", on: { click: () => { st.status = ""; st.errand = ""; statusSel.value = ""; watchSel.value = ""; st.page = 1; load(); } } }) })
        : emptyState({ title: "No checks yet", text: "Every scheduled or manual check is recorded here with its result and timing." })));
      return;
    }
    let lastDay = null;
    for (const c of d.items) {
      const dd = day(c.at);
      if (dd !== lastDay) { tbody.append(el("tr", { class: "day-row" }, el("th", { colspan: COLS, scope: "rowgroup", text: dd }))); lastDay = dd; }
      tbody.append(el("tr", { class: c.status === "error" ? "is-error" : "" },
        el("td", { class: "nowrap mono muted" }, el("time", { datetime: c.at, title: exact(c.at), text: clock(c.at) })),
        el("td", null, c.errand_id ? el("a", { class: "cell-main", href: "#/watches/" + encodeURIComponent(c.errand_id), text: c.errand_name || "Watch" }) : el("span", { class: "muted", text: c.errand_name || "—" }),
          c.value !== null && c.value !== undefined ? el("span", { class: "cell-sub only-narrow mono", text: valueText(c.value) }) : null),
        el("td", { class: "nowrap" }, checkChip(c.status)),
        el("td", { class: "num mono col-md" }, valueText(c.value)),
        el("td", { class: "num col-md mono muted" }, duration(c.duration_ms)),
        el("td", { class: "col-md nowrap" }, c.rendered ? el("span", { class: "tag", text: "JS" }) : null, " ", c.llm_used ? el("span", { class: "tag", text: "LLM" }) : null),
        el("td", { class: "col-lg msg" }, el("span", { class: "cell-clip", title: c.message || "", text: c.message || "" }))));
    }
    pagerBox.append(pager(d, (p, per) => { st.page = p; st.per = per; load(); }, { sizes: [25, 50, 100], noun: "checks" }));
  }

  load();
  return poll(() => { if (st.page === 1) load(true); }, 10000);
}
