/* Server-side pagination bar: "1–25 of 312", page size, prev/next and page numbers. */
import { el, icon } from "../dom.js";
import { number } from "../format.js";

function pageList(cur, pages) {
  if (pages <= 7) return Array.from({ length: pages }, (_, i) => i + 1);
  const out = [1];
  const lo = Math.max(2, cur - 1), hi = Math.min(pages - 1, cur + 1);
  if (lo > 2) out.push("gap");
  for (let p = lo; p <= hi; p++) out.push(p);
  if (hi < pages - 1) out.push("gap");
  out.push(pages);
  return out;
}

/** data: {page, pages, total, per_page}; onChange(page, perPage). sizes=false hides the size picker. */
export function pager(data, onChange, { sizes = [10, 25, 50], noun = "" } = {}) {
  const { page, pages, total, per_page: per } = data;
  const from = total === 0 ? 0 : (page - 1) * per + 1;
  const to = Math.min(total, page * per);

  const left = el("div", { class: "pager__left" });
  if (sizes) {
    const id = "ps-" + Math.random().toString(36).slice(2, 7);
    const sel = el("select", { class: "select", id, "aria-label": "Rows per page" },
      sizes.map((s) => el("option", { value: String(s), text: String(s), selected: s === per })));
    sel.addEventListener("change", () => onChange(1, Number(sel.value)));
    left.append(el("label", { class: "pager__size", for: id }, el("span", { text: "Rows" }), sel));
  }
  left.append(el("span", { text: number(from) + "–" + number(to) + " of " + number(total) + (noun ? " " + noun : "") }));

  const nav = el("nav", { class: "pager__pages", "aria-label": "Pagination" });
  const prev = el("button", { type: "button", class: "pager__btn", "aria-label": "Previous page", disabled: page <= 1 }, icon("chev-left", "icon--sm"));
  prev.addEventListener("click", () => onChange(page - 1, per));
  nav.append(prev);
  for (const p of pageList(page, pages)) {
    if (p === "gap") { nav.append(el("span", { class: "pager__gap", text: "…", "aria-hidden": "true" })); continue; }
    const b = el("button", { type: "button", class: "pager__btn", text: String(p), "aria-label": "Page " + p, "aria-current": p === page ? "page" : null });
    b.addEventListener("click", () => onChange(p, per));
    nav.append(b);
  }
  const next = el("button", { type: "button", class: "pager__btn", "aria-label": "Next page", disabled: page >= pages }, icon("chev-right", "icon--sm"));
  next.addEventListener("click", () => onChange(page + 1, per));
  nav.append(next);

  return el("div", { class: "pager" }, left, pages > 1 ? nav : null);
}
