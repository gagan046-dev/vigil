/* API client. All endpoints are same-origin under /api (nginx proxies to vigil-api). */

const DEFAULT_TIMEOUT = 15000;

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

function detail(body, status) {
  if (body && typeof body === "object") {
    const d = body.detail !== undefined ? body.detail : body.message;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d.map((e) => {
        const loc = Array.isArray(e.loc) ? e.loc.filter((x) => x !== "body" && x !== "spec").join(".") : "";
        return (loc ? loc + ": " : "") + (e.msg || "invalid");
      }).join("; ");
    }
  }
  if (status === 0) return "Cannot reach the Vigil API.";
  return "Request failed (HTTP " + status + ").";
}

const listeners = new Set();
export function onConnection(fn) { listeners.add(fn); }
function report(ok) { for (const fn of listeners) fn(ok); }

export async function request(method, path, body, timeout = DEFAULT_TIMEOUT) {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), timeout);
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json", Accept: "application/json" } : { Accept: "application/json" },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctl.signal,
      cache: "no-store",
      credentials: "same-origin",
    });
  } catch (err) {
    clearTimeout(timer);
    report(false);
    if (err && err.name === "AbortError") throw new ApiError("The request timed out.", 0);
    throw new ApiError("Cannot reach the Vigil API.", 0);
  }
  clearTimeout(timer);
  report(res.status !== 502 && res.status !== 503);
  if (res.status === 204) return null;
  let data = null;
  const text = await res.text();
  if (text) { try { data = JSON.parse(text); } catch (_) { data = null; } }
  if (!res.ok) throw new ApiError(detail(data, res.status), res.status);
  return data;
}

function qs(params) {
  const u = new URLSearchParams();
  for (const [k, v] of Object.entries(params || {})) {
    if (v === undefined || v === null || v === "") continue;
    u.set(k, String(v));
  }
  const s = u.toString();
  return s ? "?" + s : "";
}

/** Normalise a paginated envelope {items,total,page,per_page,pages}; tolerates a bare array. */
export function toPage(data, pageNo = 1, perPage = 25) {
  if (Array.isArray(data)) {
    return { items: data, total: data.length, page: 1, per_page: data.length || perPage, pages: 1 };
  }
  const d = data || {};
  const items = Array.isArray(d.items) ? d.items : [];
  const total = Number(d.total ?? items.length) || 0;
  const per = Number(d.per_page) || perPage;
  return { items, total, page: Number(d.page) || pageNo, per_page: per, pages: Number(d.pages) || Math.max(1, Math.ceil(total / per)) };
}

const enc = encodeURIComponent;
const paged = (path, p) => request("GET", path + qs(p)).then((d) => toPage(d, p && p.page, p && p.per_page));

export const api = {
  system: () => request("GET", "/api/system"),

  plan: (text) => request("POST", "/api/plan", { text }, 45000),
  dryrun: (spec) => request("POST", "/api/dryrun", { spec }, 75000),

  errands: (p) => paged("/api/errands", p),
  errand: (id) => request("GET", "/api/errands/" + enc(id)),
  history: (id, days) => request("GET", "/api/errands/" + enc(id) + "/history" + qs({ days })),
  errandChecks: (id, p) => paged("/api/errands/" + enc(id) + "/checks", p),
  createErrand: (spec, prompt) => request("POST", "/api/errands", { spec, prompt: prompt || null }),
  setActive: (id, active) => request("PATCH", "/api/errands/" + enc(id), { active }),
  deleteErrand: (id) => request("DELETE", "/api/errands/" + enc(id)),
  checkNow: (id) => request("POST", "/api/errands/" + enc(id) + "/check"),

  log: (p) => paged("/api/log", p),

  inbox: (p) => paged("/api/inbox", p),
  unreadCount: () => request("GET", "/api/inbox/unread-count"),
  markRead: (id) => request("POST", "/api/inbox/" + enc(id) + "/read"),
  markAllRead: () => request("POST", "/api/inbox/read-all"),

  compare: (query, maxPages) => request("POST", "/api/compare", maxPages ? { query, max_pages: maxPages } : { query }),
  compareRun: (id) => request("GET", "/api/compare/" + enc(id)),
  compareHistory: (p) => paged("/api/compare", p),
  trackCompare: (id, body) => request("POST", "/api/compare/" + enc(id) + "/track", body),
};
