/* Formatting: INR with Indian digit grouping, relative/exact times, schedules, conditions. */

const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const num = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const exactFmt = new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "medium" });
const shortFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hour12: false });
const dayFmt = new Intl.DateTimeFormat("en-IN", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const timeFmt = new Intl.DateTimeFormat("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const dateFmt = new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" });

export function isNum(v) { return v !== null && v !== undefined && v !== "" && !isNaN(Number(v)); }

export function money(v, currency) {
  if (!isNum(v)) return "—";
  const n = Number(v);
  const c = currency ? String(currency).trim() : "";
  if (c && !/^(INR|₹|Rs\.?)$/i.test(c)) {
    if (!/^[A-Z]{3}$/.test(c)) return c + num.format(n);
    try {
      return new Intl.NumberFormat("en-IN", { style: "currency", currency: c, maximumFractionDigits: 2 }).format(n);
    } catch (_) { return c + " " + num.format(n); }
  }
  return Number.isInteger(n) ? inr0.format(n) : inr2.format(n);
}

export function number(v) { return isNum(v) ? num.format(Number(v)) : "—"; }

export function parseTime(v) {
  if (!v) return null;
  const d = new Date(v);
  return isNaN(d.getTime()) ? null : d;
}

export function exact(v) { const d = parseTime(v); return d ? exactFmt.format(d) : ""; }
export function short(v) { const d = parseTime(v); return d ? shortFmt.format(d) : "—"; }
export function day(v) { const d = parseTime(v); return d ? dayFmt.format(d) : ""; }
export function clock(v) { const d = parseTime(v); return d ? timeFmt.format(d) : "—"; }
export function dateOnly(v) { const d = parseTime(v); return d ? dateFmt.format(d) : ""; }

export function relative(v, now = Date.now()) {
  const d = parseTime(v);
  if (!d) return "never";
  const diff = Math.round((d.getTime() - now) / 1000);
  const s = Math.abs(diff);
  const fut = diff > 0;
  let out;
  if (s < 45) return fut ? "in a few seconds" : "just now";
  if (s < 3600) out = Math.round(s / 60) + " min";
  else if (s < 86400) out = Math.round(s / 3600) + " h";
  else if (s < 86400 * 30) out = Math.round(s / 86400) + " d";
  else return short(v);
  return fut ? "in " + out : out + " ago";
}

export function duration(ms) {
  if (!isNum(ms)) return "—";
  const n = Number(ms);
  if (n < 1000) return n + " ms";
  return (n / 1000).toFixed(n < 10000 ? 1 : 0) + " s";
}

export function everyLabel(n) {
  n = Number(n);
  if (!n) return "—";
  if (n < 60) return "Every " + n + " min";
  if (n === 60) return "Hourly";
  if (n === 1440) return "Daily, 08:00";
  return "Every " + n / 60 + " h";
}

export function pct(v, digits = 0) {
  if (!isNum(v)) return "—";
  return (Number(v) * 100).toFixed(digits) + "%";
}

export function hostOf(url) {
  try { return new URL(url).hostname.replace(/^www\./, ""); } catch (_) { return url || ""; }
}

export const KIND_LABELS = {
  price_below: "Price below",
  price_above: "Price above",
  best_price_below: "Best price below",
  text_appears: "Text appears",
  text_disappears: "Text disappears",
  page_changed: "Page changes",
};

export function conditionText(spec) {
  if (!spec) return "";
  switch (spec.kind) {
    case "price_below": return "Price below " + money(spec.threshold);
    case "price_above": return "Price above " + money(spec.threshold);
    case "best_price_below":
      return isNum(spec.threshold) ? "Best price below " + money(spec.threshold) : "Best price drops";
    case "text_appears": return "“" + (spec.keyword || "") + "” appears";
    case "text_disappears": return "“" + (spec.keyword || "") + "” disappears";
    case "page_changed": return "Page changes";
    default: return spec.kind || "";
  }
}

export function isPriceKind(kind) { return kind === "price_below" || kind === "price_above" || kind === "best_price_below"; }

export function plural(n, one, many) { return number(n) + " " + (Number(n) === 1 ? one : (many || one + "s")); }

/** Estimate the next run from the interval (cron semantics: every n min, every n hours on the hour, daily 08:00). */
export function nextRun(everyMinutes, from = new Date()) {
  const n = Number(everyMinutes);
  if (!n) return null;
  const d = new Date(from.getTime());
  d.setSeconds(0, 0);
  if (n < 60) {
    d.setMinutes(Math.floor(d.getMinutes() / n) * n + n);
  } else if (n < 1440) {
    const h = n / 60;
    d.setMinutes(0);
    d.setHours(Math.floor(d.getHours() / h) * h + h);
  } else {
    d.setHours(8, 0);
    if (d <= from) d.setDate(d.getDate() + 1);
  }
  return d;
}
