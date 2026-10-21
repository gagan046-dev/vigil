/* Errand actions shared by the watches list and the watch detail page. */
import { api } from "../api.js";
import { toast, confirmDialog } from "../components/feedback.js";
import { watchesChanged } from "../store.js";

export function errandMenuItems(e, { onDone, onDeleted, includeOpen = true } = {}) {
  const items = [];
  if (includeOpen) items.push({ label: "Open", icon: "chev-right", onSelect: () => { location.hash = "#/watches/" + encodeURIComponent(e.id); } });
  items.push({ label: "Check now", icon: "refresh", onSelect: () => checkNow(e, onDone) });
  items.push(e.active === false
    ? { label: "Resume", icon: "play", onSelect: () => setActive(e, true, onDone) }
    : { label: "Pause", icon: "pause", onSelect: () => setActive(e, false, onDone) });
  items.push("sep");
  items.push({ label: "Delete…", icon: "trash", danger: true, onSelect: () => remove(e, onDeleted || onDone) });
  return items;
}

export async function checkNow(e, onDone) {
  try {
    const r = await api.checkNow(e.id);
    toast(r && r.queued === false ? "A check for “" + e.name + "” is already queued." : "Check queued for “" + e.name + "”.");
    if (onDone) setTimeout(onDone, 2500);
  } catch (err) { toast(err.message, { error: true }); }
}

export async function setActive(e, active, onDone) {
  try {
    await api.setActive(e.id, active);
    toast((active ? "Resumed " : "Paused ") + "“" + e.name + "”.");
    watchesChanged();
    if (onDone) onDone();
  } catch (err) { toast(err.message, { error: true }); }
}

export async function remove(e, onDone) {
  const ok = await confirmDialog({
    title: "Delete this watch?",
    text: "“" + e.name + "” and its check history will be removed. Its schedule is cancelled. This cannot be undone.",
    confirmLabel: "Delete watch",
    danger: true,
  });
  if (!ok) return;
  try {
    await api.deleteErrand(e.id);
    toast("Deleted “" + e.name + "”.");
    watchesChanged();
    if (onDone) onDone();
  } catch (err) { toast(err.message, { error: true }); }
}

/** setInterval that pauses while the tab is hidden. Returns a stop function. */
export function poll(fn, ms) {
  let t = null;
  const start = () => { stop(); t = setInterval(() => { if (!document.hidden) fn(); }, ms); };
  const stop = () => { if (t) clearInterval(t); t = null; };
  const vis = () => { if (!document.hidden) fn(); };
  document.addEventListener("visibilitychange", vis);
  start();
  return () => { stop(); document.removeEventListener("visibilitychange", vis); };
}

export function debounce(fn, ms) {
  let t = null;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
}
