/* Compare: search a product (name or URL) across sellers; recent comparisons. */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { money, hostOf, plural, isNum } from "../format.js";
import { errorBanner, emptyState, skeletonRows, timeEl, fullRow } from "../components/feedback.js";
import { pager } from "../components/pager.js";
import { getSystem, hashQuery, setHashQuery, intParam } from "../store.js";
import { poll } from "./common.js";
import { runStatusChip } from "./compare-run.js";

export function render(main, _params, ctx) {
  const q0 = hashQuery();
  const st = { page: intParam(q0.page, 1), per: [10, 25, 50].includes(Number(q0.per_page)) ? Number(q0.per_page) : 10, data: null, error: null };

  const input = el("input", { class: "input compare-search__input", id: "cmp-q", type: "text", maxlength: 500, autocomplete: "off",
    placeholder: "Product name or a product page URL", "data-search": "", value: q0.query || "" });
  const submit = el("button", { type: "submit", class: "btn btn--primary btn--lg" }, "Compare");
  const formErr = el("div");
  const sellers = el("p", { class: "compare-search__sellers" });
  const form = el("form", { class: "compare-search", autocomplete: "off", novalidate: true },
    el("label", { class: "compare-search__label", for: "cmp-q", text: "Find the best price" }),
    el("div", { class: "compare-search__row" }, el("div", { class: "search search--lg" }, icon("search"), input), submit),
    sellers, formErr);

  const tbody = el("tbody");
  const pagerBox = el("div");
  main.append(
    el("section", { class: "panel compare-panel", "aria-label": "Compare prices" }, el("div", { class: "panel__body" }, form)),
    el("section", { class: "panel", "aria-labelledby": "h-recent" },
      el("div", { class: "panel__head" }, el("h2", { class: "panel__title", id: "h-recent", text: "Recent comparisons" })),
      el("div", { class: "table-wrap" }, el("table", { class: "table" },
        el("thead", null, el("tr", null,
          el("th", { scope: "col", text: "Product" }),
          el("th", { scope: "col", class: "num", text: "Best price" }),
          el("th", { scope: "col", class: "col-md", text: "Seller" }),
          el("th", { scope: "col", class: "num col-lg", text: "Offers" }),
          el("th", { scope: "col", text: "Status" }),
          el("th", { scope: "col", class: "col-md", text: "Run" }))),
        tbody)),
      pagerBox));

  getSystem().then((s) => {
    if (!ctx.alive()) return;
    const list = (Array.isArray(s.compare_sellers) ? s.compare_sellers : String(s.compare_sellers || "").split(",").map((x) => x.trim()).filter(Boolean))
      .map((x) => (x && typeof x === "object" ? x.name || x.domain : String(x)));
    clear(sellers);
    if (list.length) {
      sellers.append("Searches " + plural(list.length, "seller") + ": ");
      sellers.append(el("span", { class: "compare-search__list", text: list.join(", ") }));
      if (s.search_provider === "demo") sellers.append(el("span", { class: "tag", text: "Demo search" }));
    }
  }).catch(() => null);

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const q = input.value.trim();
    clear(formErr);
    if (q.length < 3) { formErr.append(errorBanner(new Error("Enter a product name or paste a product page URL."))); input.focus(); return; }
    submit.disabled = true;
    mount(submit, el("span", { class: "spinner", "aria-hidden": "true" }), "Starting…");
    try {
      const r = await api.compare(q);
      location.hash = "#/compare/" + encodeURIComponent(r.id);
    } catch (e) {
      submit.disabled = false;
      mount(submit, "Compare");
      formErr.append(errorBanner(e));
    }
  });

  async function load(silent) {
    setHashQuery({ page: st.page, per_page: st.per }, { page: 1, per_page: 10 });
    if (!silent && !st.data) { clear(tbody); tbody.append(...skeletonRows(["60%", "50%", "50%", "30%", "50%", "40%"], 5)); }
    try {
      const d = await api.compareHistory({ page: st.page, per_page: st.per });
      if (d.page > d.pages && d.total > 0) { st.page = d.pages; return load(silent); }
      st.data = d;
      st.error = null;
    } catch (e) { st.error = e; }
    if (!ctx.alive()) return;
    paint();
  }

  function paint() {
    clear(tbody); clear(pagerBox);
    if (st.error && !st.data) { tbody.append(fullRow(6, el("div", { class: "pad" }, errorBanner(st.error, () => load())))); return; }
    const d = st.data;
    if (!d.items.length) {
      tbody.append(fullRow(6, emptyState({ title: "No comparisons yet", text: "Search for a product above. Vigil looks it up at each seller, reads the listed price and keeps only listings that match the same model." })));
      return;
    }
    for (const r of d.items) {
      const href = "#/compare/" + encodeURIComponent(r.id);
      const best = r.best || null;
      const offers = Array.isArray(r.offers) ? r.offers : [];
      const total = isNum(r.offers_count) ? Number(r.offers_count) : offers.length;
      const okCount = r.notes && isNum(r.notes.offers) ? Number(r.notes.offers) : offers.filter((o) => !o.error && o.price !== null && o.price !== undefined).length;
      const title = (r.product && r.product.title) || r.query;
      tbody.append(el("tr", null,
        el("td", null, el("a", { class: "cell-main", href, text: title }),
          title !== r.query ? el("span", { class: "cell-sub", text: r.query }) : null),
        el("td", { class: "num value" }, best ? money(best.price, best.currency) : "—"),
        el("td", { class: "col-md" }, best ? (best.seller_name || best.seller || hostOf(best.url)) : el("span", { class: "muted", text: "—" })),
        el("td", { class: "num col-lg muted", text: total ? okCount + " of " + total : "—" }),
        el("td", { class: "nowrap" }, runStatusChip(r.status)),
        el("td", { class: "col-md nowrap muted" }, timeEl(r.created_at))));
    }
    pagerBox.append(pager(d, (p, per) => { st.page = p; st.per = per; load(); }, { noun: "comparisons" }));
  }

  load();
  setTimeout(() => { if (ctx.alive() && !input.value) input.focus(); }, 0);
  return poll(() => {
    if (st.data && st.data.items.some((r) => !["done", "error"].includes(r.status))) load(true);
  }, 3000);
}
