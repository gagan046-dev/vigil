/* Small shared state: cached /api/system, unread-count broadcast, URL query helpers. */
import { api } from "./api.js";

let systemCache = null;
let systemAt = 0;

export async function getSystem(force = false) {
  if (!force && systemCache && Date.now() - systemAt < 30000) return systemCache;
  systemCache = await api.system();
  systemAt = Date.now();
  return systemCache;
}
export function cachedSystem() { return systemCache; }

export const ALLOWED_EVERY = [1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440];
export function allowedIntervals() {
  const floor = Number(systemCache && systemCache.min_interval_minutes) || 1;
  return ALLOWED_EVERY.filter((n) => n >= floor);
}

/** Pages call this after they change read state so the sidebar badge refreshes now. */
export function inboxChanged() { window.dispatchEvent(new CustomEvent("vigil:inbox")); }
export function watchesChanged() { window.dispatchEvent(new CustomEvent("vigil:watches")); }

/** Current hash query as an object. */
export function hashQuery() {
  const h = location.hash || "";
  const i = h.indexOf("?");
  return Object.fromEntries(new URLSearchParams(i >= 0 ? h.slice(i + 1) : ""));
}

/** Update the hash query without triggering a re-route (keeps back/refresh meaningful). */
export function setHashQuery(obj, defaults = {}) {
  const h = location.hash || "#/";
  const path = h.split("?")[0];
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(obj)) {
    if (v === undefined || v === null || v === "" || String(v) === String(defaults[k])) continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  history.replaceState(null, "", path + (s ? "?" + s : ""));
}

export function intParam(v, def, min = 1, max = 100000) {
  const n = parseInt(v, 10);
  if (!Number.isFinite(n)) return def;
  return Math.min(max, Math.max(min, n));
}
