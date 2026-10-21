/* Inbox: alerts from watches. Unread emphasis, All/Unread filter, mark read, pagination. */
import { el, icon, mount, clear } from "../dom.js";
import { api } from "../api.js";
import { exact, relative, day } from "../format.js";
import { errorBanner, emptyState, skeletonBlock, toast } from "../components/feedback.js";
import { pager } from "../components/pager.js";
import { hashQuery, setHashQuery, intParam, inboxChanged } from "../store.js";
import { poll } from "./common.js";

export function render(main, _params, ctx) {
  const q0 = hashQuery();
  const st = {
    page: intParam(q0.page, 1),
    per: [10, 25, 50].includes(Number(q0.per_page)) ? Number(q0.per_page) : 25,
    unread: q0.filter === "unread",
    data: null, error: null,
  };
  const seg = el("div", { class: "segmented", role: "group", "aria-label": "Filter" });
  const allBtn = el("button", { type: "button", class: "btn btn--sm" }, icon("check-all", "icon--sm"), "Mark all as read");
  const list = el("ul", { class: "inbox", role: "list" });
  const pagerBox = el("div");
  const bannerBox = el("div");

  main.append(bannerBox, el("section", { class: "panel", "aria-label": "Inbox" },
    el("div", { class: "toolbar" }, seg, el("span", { class: "toolbar__spacer" }), allBtn),
    list, pagerBox));

  function paintSeg() {
    clear(seg);
    for (const [k, label] of [[false, "All"], [true, "Unread"]]) {
      const b = el("button", { type: "button", class: "segmented__btn", text: label, "aria-pressed": String(st.unread === k) });
      b.addEventListener("click", () => { st.unread = k; st.page = 1; paintSeg(); load(); });
      seg.append(b);
    }
  }

  async function load(silent) {
    setHashQuery({ page: st.page, per_page: st.per, filter: st.unread ? "unread" : "" }, { page: 1, per_page: 25 });
    if (!silent && !st.data) mount(list, el("li", { class: "inbox__item" }, skeletonBlock(2)), el("li", { class: "inbox__item" }, skeletonBlock(2)), el("li", { class: "inbox__item" }, skeletonBlock(2)));
    try {
      const d = await api.inbox({ page: st.page, per_page: st.per, unread: st.unread ? "true" : "" });
      if (!ctx.alive()) return;
      if (d.page > d.pages && d.total > 0) { st.page = d.pages; return load(); }
      st.data = d; st.error = null;
    } catch (e) { if (!ctx.alive()) return; st.error = e; }
    paint();
  }

  async function markRead(n, node) {
    try {
      await api.markRead(n.id);
      n.read = true;
      inboxChanged();
      if (st.unread) load(true); else node.replaceWith(item(n));
    } catch (e) { toast(e.message, { error: true }); }
  }

  allBtn.addEventListener("click", async () => {
    allBtn.disabled = true;
    try { await api.markAllRead(); toast("All alerts marked as read."); inboxChanged(); await load(true); }
    catch (e) { toast(e.message, { error: true }); }
    allBtn.disabled = false;
  });

  function item(n) {
    const node = el("li", { class: "inbox__item" + (n.read ? "" : " is-unread") });
    const markBtn = n.read ? null : el("button", { type: "button", class: "btn btn--sm btn--ghost", "aria-label": "Mark “" + n.title + "” as read" }, icon("check", "icon--sm"), "Mark read");
    if (markBtn) markBtn.addEventListener("click", () => markRead(n, node));
    node.append(
      el("span", { class: "inbox__dot", "aria-hidden": "true" }),
      el("div", { class: "inbox__content" },
        el("p", { class: "inbox__title" }, n.read ? null : el("span", { class: "visually-hidden", text: "Unread: " }), n.title),
        n.body ? el("p", { class: "inbox__body", text: n.body }) : null,
        el("p", { class: "inbox__meta" },
          n.errand_id ? el("a", { href: "#/watches/" + encodeURIComponent(n.errand_id), text: n.errand_name || "Open watch" }) : el("span", { text: n.errand_name || "Deleted watch" }),
          n.delivered_to ? el("span", { text: " · sent to " + n.delivered_to }) : null)),
      el("div", { class: "inbox__side" },
        el("time", { datetime: n.at, title: exact(n.at), text: relative(n.at) }),
        markBtn));
    return node;
  }

  function paint() {
    clear(list); clear(pagerBox); clear(bannerBox);
    if (st.error && st.data) bannerBox.append(errorBanner(st.error, () => load()));
    if (st.error && !st.data) { list.append(el("li", { class: "pad" }, errorBanner(st.error, () => load()))); return; }
    const d = st.data;
    allBtn.disabled = !d.items.some((n) => !n.read) && !st.unread;
    if (!d.items.length) {
      list.append(el("li", null, st.unread
        ? emptyState({ title: "You are all caught up", text: "No unread alerts.", action: el("button", { type: "button", class: "btn", text: "Show all alerts", on: { click: () => { st.unread = false; st.page = 1; paintSeg(); load(); } } }) })
        : emptyState({ title: "No alerts yet", text: "When a watch’s condition becomes true, the alert lands here (and on any configured webhook).", action: el("a", { class: "btn", href: "#/watches", text: "Go to watches" }) })));
      return;
    }
    let lastDay = null;
    for (const n of d.items) {
      const dd = day(n.at);
      if (dd !== lastDay) { list.append(el("li", { class: "inbox__day", text: dd })); lastDay = dd; }
      list.append(item(n));
    }
    pagerBox.append(pager(d, (p, per) => { st.page = p; st.per = per; load(); }, { noun: "alerts" }));
  }

  paintSeg();
  load();
  return poll(() => load(true), 20000);
}
