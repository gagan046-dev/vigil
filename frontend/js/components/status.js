/* Status chips and value deltas, shared by watches, detail, activity. */
import { el, icon } from "../dom.js";
import { isNum, money, number, isPriceKind } from "../format.js";

export function errandState(e) {
  if (e.failing || Number(e.consecutive_failures) >= 3) return "failing";
  if (e.active === false) return "paused";
  if (e.condition_met) return "met";
  if (!e.last_status) return "pending";
  return "active";
}

const ERRAND_CHIP = {
  failing: ["chip--danger", "Failing"],
  paused: ["chip--warn", "Paused"],
  met: ["chip--ok", "Condition met"],
  pending: ["chip--muted", "Waiting for first check"],
  active: ["chip--active", "Active"],
};

export function errandChip(e) {
  const [cls, label] = ERRAND_CHIP[errandState(e)];
  return el("span", { class: "chip " + cls, text: label });
}

const CHECK_CHIP = {
  triggered: ["chip--ok", "Triggered"],
  changed: ["chip--active", "Changed"],
  unchanged: ["chip--muted chip--plain", "Unchanged"],
  baseline: ["chip--muted", "Baseline"],
  error: ["chip--danger", "Error"],
};

export function checkChip(status) {
  const [cls, label] = CHECK_CHIP[status] || ["chip--muted", status || "—"];
  return el("span", { class: "chip " + cls, text: label });
}

export function valueText(v, kind) {
  if (!isNum(v)) return "—";
  return isPriceKind(kind) || kind === undefined ? money(v) : number(v);
}

/** Change vs previous value. For prices, a drop is good (green). */
export function delta(cur, prev) {
  if (!isNum(cur) || !isNum(prev)) return el("span", { class: "delta delta--flat", text: "—" });
  const d = Number(cur) - Number(prev);
  if (Math.abs(d) < 0.005) return el("span", { class: "delta delta--flat", text: "No change" });
  const p = prev ? Math.abs(d / Number(prev)) * 100 : 0;
  const down = d < 0;
  return el("span", { class: "delta " + (down ? "delta--down" : "delta--up"), title: (down ? "Down " : "Up ") + money(Math.abs(d)) },
    icon(down ? "down" : "up"),
    money(Math.abs(d)), el("span", { class: "muted", text: " (" + p.toFixed(p < 10 ? 1 : 0) + "%)" }));
}
