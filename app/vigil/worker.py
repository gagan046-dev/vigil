"""The check worker. Run as: python -m vigil.worker

BLPOPs jobs from Redis and runs the check pipeline:
safety -> robots -> politeness -> fetch (auto-render fallback) -> text + hash
-> cheap path (unchanged) -> extract (selector / learned / heuristic / LLM / regex,
with self-healing selectors) -> evaluate (fire on transition) -> notify.
"""

from __future__ import annotations

import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from bs4 import BeautifulSoup
from prometheus_client import start_http_server

from . import queue
from .compare import CompareOutcome, Comparer, best_summary, clean_reference
from .config import Settings, load_settings
from .evaluate import condition_true, diff_summary, evaluate, unified_diff_text
from .extract import (
    css_selector_for,
    find_element_containing,
    find_price,
    format_money,
    hash_text,
    heuristic_price,
    page_label,
    page_lines,
    parse_html,
    select_price,
)
from .fetch import FetchError, FetchResult
from .llm import LLMClient
from .metrics import CHECKS_TOTAL, COMPARES_TOTAL
from .models import COMPARE_KINDS, PRICE_KINDS, TEXT_KINDS, ErrandSpec
from .search import make_search

log = logging.getLogger("vigil.worker")

FAILURE_ALERT_AT = 3
_LOADING_MARKERS = ("loading…", "loading...", "loading")


class CheckFailedError(Exception):
    pass


@dataclass
class PriceResult:
    value: float
    currency: str | None
    method: str  # selector | learned | heuristic | llm | regex
    learned: str | None = None  # selector to store as selector_learned
    healed_from: str | None = None
    llm_used: bool = False


@dataclass
class Observation:
    rendered: bool
    duration_ms: int
    lines: list[str]
    text: str
    hash: str
    label: str | None
    soup: BeautifulSoup
    selector_matched: bool
    has_script: bool = True
    auto_rendered: bool = False
    price: PriceResult | None = None
    notes: list[str] = field(default_factory=list)


LLM_EXTRACT_SYSTEM = (
    "You extract the current selling price of the main product from web page text. Reply with ONE "
    'JSON object: {"value": <number, no currency symbol or commas>, "snippet": "<the exact price text '
    'as it appears on the page>"}. If there is no price reply {"value": null, "snippet": null}.'
)
LLM_SUMMARY_SYSTEM = (
    "You summarise what changed on a web page for a busy person. You get a unified diff of the page's "
    'visible text. Reply with ONE JSON object: {"summary": "<at most 3 short lines>"}.'
)


def _currency_of(match_text: str) -> str | None:
    pm = find_price(match_text)
    return pm.currency if pm else None


class CheckRunner:
    def __init__(self, store, r, fetcher, llm: LLMClient | None, settings: Settings, search=None):
        self.store = store
        self.r = r
        self.fetcher = fetcher
        self.llm = llm
        self.settings = settings
        self._search = search

    @property
    def search(self):
        if self._search is None:
            self._search = make_search(self.settings)
        return self._search

    # ------------------------------------------------------------ price comparison

    def comparer(self) -> Comparer:
        return Comparer(self.fetcher, self.search, self.llm, self.settings)

    def compare(self, compare_id: str, query: str, max_pages: int | None = None) -> CompareOutcome:
        """Run the comparison pipeline and keep its compare_runs row up to date."""

        def progress(status: str, rows: list[dict], notes: dict) -> None:
            self.store.update_compare(compare_id, status=status, offers=rows, notes=notes)

        try:
            outcome = self.comparer().run(query, max_pages=max_pages, progress=progress)
        except Exception as exc:
            log.exception("comparison crashed")
            self.store.update_compare(
                compare_id,
                status="error",
                notes={"messages": [f"internal error ({type(exc).__name__})"]},
                finished_at=_now(),
            )
            COMPARES_TOTAL.labels(result="error").inc()
            raise
        self.store.update_compare(
            compare_id,
            status="done",
            product=outcome.product,
            offers=outcome.offers,
            best=outcome.best,
            notes=outcome.notes,
            finished_at=_now(),
        )
        COMPARES_TOTAL.labels(result="found" if outcome.best else "none").inc()
        return outcome

    def run_compare(self, compare_id: str, max_pages: int | None = None) -> str | None:
        row = self.store.get_compare(compare_id)
        if row is None:
            log.info("comparison %s no longer exists; skipping", compare_id)
            return None
        try:
            self.compare(compare_id, row["query"], max_pages)
        except Exception:
            return "error"
        return "done"

    def _run_best_price(self, errand: dict, spec: ErrandSpec, started: float) -> str:
        eid = errand["id"]
        query = spec.query or spec.url or ""
        run = self.store.create_compare(query, errand_id=eid)
        try:
            outcome = self.compare(run["id"], query)
        except Exception as exc:
            return self._record_failure(errand, spec, f"internal error ({type(exc).__name__})", started)
        finally:
            try:
                self.store.prune_compares(eid, keep=50)
            except Exception:
                log.warning("could not prune old comparisons")
        duration = int((time.monotonic() - started) * 1000)
        best = outcome.best
        n = outcome.notes
        tally = f"{n.get('offers', 0)} offers, {n.get('failed', 0)} failed, {n.get('rejected', 0)} rejected"
        if best is None:
            return self._record_failure(errand, spec, f"no seller returned a matching price ({tally})", started)

        value = float(best["price"])
        prev = errand.get("last_value")
        threshold = spec.threshold
        if threshold:
            cond = value < threshold
            fire = cond and not bool(errand.get("condition_met"))
        else:  # no target: every drop below the previous best is news
            cond = prev is not None and value < float(prev) - 0.005
            fire = cond
        if fire:
            status = "triggered"
        elif errand.get("last_hash") is None:
            status = "baseline"
        elif prev is not None and abs(float(prev) - value) >= 0.005:
            status = "changed"
        else:
            status = "unchanged"
        cur = best.get("currency") or "₹"
        lines = [
            f"{o['seller_name']}: {format_money(o['price'], o.get('currency') or cur)}"
            + (" (out of stock)" if o.get("in_stock") is False else "")
            for o in outcome.offers
            if not o.get("error")
        ]
        text = "\n".join(lines)
        messages = [f"best {best_summary(best)}; {tally}"]
        if fire:
            title = clean_reference(spec.query or outcome.product.get("title") or errand["name"], 8)
            target = f"target < {format_money(threshold, cur)}" if threshold else f"was {format_money(prev, cur)}"
            note = f"Best price: {title} at {best['seller_name']} {format_money(value, cur)} ({target})"
            queue.push_notify(self.r, eid, note[:300], f"{errand['name']}\n{best.get('url') or ''}")
            messages.insert(0, note)
        self.store.update_errand_state(
            eid,
            last_checked_at=_now(),
            last_status=status,
            last_value=value,
            last_hash=hash_text(f"{best.get('url')}|{value}|{text}"),
            last_text=text,
            condition_met=bool(cond) if threshold else False,
            consecutive_failures=0,
        )
        self.store.add_check(eid, status, duration, value, False, outcome.llm_used, "; ".join(messages)[:1000])
        CHECKS_TOTAL.labels(status=status).inc()
        return status

    # ------------------------------------------------------------ observation

    def _analyse(self, fr: FetchResult, spec: ErrandSpec) -> Observation:
        soup = parse_html(fr.html)
        lines, matched = page_lines(soup, spec.selector)
        text = "\n".join(lines)
        obs = Observation(fr.rendered, fr.duration_ms, lines, text, hash_text(text), page_label(soup), soup, matched)
        obs.has_script = "<script" in fr.html.lower()
        return obs

    @staticmethod
    def _looks_unrendered(obs: Observation) -> bool:
        flat = " ".join(obs.lines)
        low = flat.lower()
        return len(flat) < 300 or any(m in low for m in _LOADING_MARKERS)

    def _deterministic_price(self, obs: Observation, spec: ErrandSpec, learned: str | None) -> PriceResult | None:
        broken: str | None = None
        if spec.selector:
            hit = select_price(obs.soup, spec.selector)
            if hit:
                return PriceResult(hit[0].value, hit[0].currency, "selector")
            broken = spec.selector
        if learned:
            hit = select_price(obs.soup, learned)
            if hit:
                return PriceResult(hit[0].value, hit[0].currency, "learned", learned=learned)
            broken = learned
        hit = heuristic_price(obs.soup)
        if hit:
            return self._learn(PriceResult(hit[0].value, hit[0].currency, "heuristic"), hit[1], obs, broken)
        pm = find_price(" ".join(obs.lines), require_currency=True)
        if pm:
            el = find_element_containing(obs.soup, pm.text)
            return self._learn(PriceResult(pm.value, pm.currency, "regex"), el, obs, broken)
        return None

    def _learn(self, res: PriceResult, el, obs: Observation, broken: str | None) -> PriceResult:
        sel = css_selector_for(el, obs.soup) if el is not None else None
        if sel:
            hit = select_price(obs.soup, sel)
            if hit and abs(hit[0].value - res.value) < 0.005:
                res.learned = sel
                if broken and broken != sel:
                    res.healed_from = broken
        return res

    def _llm_price(self, obs: Observation, spec: ErrandSpec, learned: str | None) -> PriceResult | None:
        if self.llm is None or not self.llm.available():
            return None
        reply = self.llm.chat_json(
            LLM_EXTRACT_SYSTEM,
            f"Page: {spec.url}\nTitle: {obs.label or ''}\nText:\n{obs.text[:6000]}",
            purpose="extract",
        )
        if not reply:
            return None
        try:
            value = float(reply.get("value"))
        except (TypeError, ValueError):
            return None
        if value <= 0:
            return None
        snippet = str(reply.get("snippet") or "")[:120]
        res = PriceResult(value, _currency_of(snippet), "llm", llm_used=True)
        if snippet:
            el = find_element_containing(obs.soup, snippet)
            broken = learned or spec.selector
            res = self._learn(res, el, obs, broken)
        return res

    def observe(
        self, spec: ErrandSpec, *, last_hash: str | None = None, learned: str | None = None, cheap_path: bool = True
    ) -> tuple[Observation, bool]:
        """Fetch + analyse. Returns (observation, unchanged)."""
        fr = self.fetcher.fetch(spec.url, render=spec.render)
        obs = self._analyse(fr, spec)
        if cheap_path and last_hash and obs.hash == last_hash:
            return obs, True

        needs_render = False
        if not obs.rendered:
            if spec.kind in PRICE_KINDS:
                obs.price = self._deterministic_price(obs, spec, learned)
                needs_render = obs.price is None
            elif spec.kind in TEXT_KINDS:
                present = spec.keyword.lower() in " ".join(obs.lines).lower()
                needs_render = not present and self._looks_unrendered(obs)
            else:
                needs_render = self._looks_unrendered(obs)
        # a page without any <script> cannot be filled in by JavaScript: rendering would not help
        if needs_render and obs.has_script:
            try:
                fr2 = self.fetcher.fetch(spec.url, render=True)
            except FetchError as exc:
                obs.notes.append(f"JavaScript rendering failed: {exc}")
            else:
                obs = self._analyse(fr2, spec)
                obs.auto_rendered = True
                obs.duration_ms += fr.duration_ms
                if cheap_path and last_hash and obs.hash == last_hash:
                    return obs, True

        if spec.kind in PRICE_KINDS and obs.price is None:
            obs.price = self._deterministic_price(obs, spec, learned) or self._llm_price(obs, spec, learned)
            if obs.price is None:
                raise CheckFailedError("price not found on the page")
        return obs, False

    # ------------------------------------------------------------ checks

    def _summary(self, old_text: str, new_text: str) -> tuple[str, bool]:
        if self.llm is not None and self.llm.available():
            reply = self.llm.chat_json(LLM_SUMMARY_SYSTEM, unified_diff_text(old_text, new_text), purpose="summary")
            summary = (reply or {}).get("summary")
            if isinstance(summary, str) and summary.strip():
                return "\n".join(summary.strip().splitlines()[:3])[:500], True
        return diff_summary(old_text, new_text), False

    def run_check(self, errand_id: str) -> str | None:
        errand = self.store.get_errand(errand_id)
        if errand is None:
            log.info("errand %s no longer exists; skipping", errand_id)
            return None
        try:
            spec = ErrandSpec.model_validate(errand["spec"])
        except Exception:
            # specs are validated on creation; a stricter MIN_INTERVAL later must not break checks
            spec = ErrandSpec.model_construct(**errand["spec"])
        started = time.monotonic()
        if spec.kind in COMPARE_KINDS:
            try:
                return self._run_best_price(errand, spec, started)
            except Exception as exc:  # never let one bad comparison kill the worker
                log.exception("best-price check crashed for %s", errand_id)
                return self._record_failure(errand, spec, f"internal error ({type(exc).__name__})", started)
        try:
            obs, unchanged = self.observe(
                spec, last_hash=errand.get("last_hash"), learned=errand.get("selector_learned")
            )
        except (FetchError, CheckFailedError) as exc:
            return self._record_failure(errand, spec, str(exc), started)
        except Exception as exc:  # never let one bad page kill the worker
            log.exception("check crashed for %s", errand_id)
            return self._record_failure(errand, spec, f"internal error ({type(exc).__name__})", started)
        duration = int((time.monotonic() - started) * 1000)

        if unchanged:
            self.store.update_errand_state(
                errand_id, last_checked_at=_now(), last_status="unchanged", consecutive_failures=0
            )
            self.store.add_check(
                errand_id,
                "unchanged",
                duration,
                errand.get("last_value"),
                obs.rendered,
                False,
                "no change (cheap path)",
            )
            CHECKS_TOTAL.labels(status="unchanged").inc()
            return "unchanged"

        value = obs.price.value if obs.price else None
        ev = evaluate(
            spec.kind,
            prev_condition_met=bool(errand.get("condition_met")),
            prev_hash=errand.get("last_hash"),
            new_hash=obs.hash,
            value=value,
            threshold=spec.threshold,
            keyword=spec.keyword,
            text=" ".join(obs.lines),
        )
        messages: list[str] = list(obs.notes)
        llm_used = bool(obs.price and obs.price.llm_used)
        learned = errand.get("selector_learned")
        if obs.price:
            if obs.price.learned:
                learned = obs.price.learned
            if obs.price.healed_from:
                messages.append(f"selector healed: {obs.price.healed_from} -> {obs.price.learned}")
            messages.append(f"{format_money(value, obs.price.currency)} via {obs.price.method}")
        if obs.auto_rendered:
            messages.append("page needed JavaScript; rendered in a browser")

        if ev.fire:
            title, body, used = self._notification(errand, spec, obs, value)
            llm_used = llm_used or used
            queue.push_notify(self.r, errand_id, title, body)
            messages.insert(0, title)

        self.store.update_errand_state(
            errand_id,
            last_checked_at=_now(),
            last_status=ev.status,
            last_value=value,
            last_hash=obs.hash,
            last_text=obs.text,
            condition_met=ev.condition_met,
            consecutive_failures=0,
            selector_learned=learned,
        )
        self.store.add_check(
            errand_id, ev.status, duration, value, obs.rendered, llm_used, "; ".join(messages)[:1000] or None
        )
        CHECKS_TOTAL.labels(status=ev.status).inc()
        return ev.status

    def _notification(
        self, errand: dict, spec: ErrandSpec, obs: Observation, value: float | None
    ) -> tuple[str, str, bool]:
        label = obs.label or errand["name"]
        cur = obs.price.currency if obs.price else None
        if spec.kind in PRICE_KINDS:
            word, op = ("Price drop", "<") if spec.kind == "price_below" else ("Price rise", ">")
            was = errand.get("last_value")
            was_txt = f", was {format_money(was, cur)}" if was is not None else ""
            title = (
                f"{word}: {label} is {format_money(value, cur)} "
                f"(target {op} {format_money(spec.threshold, cur)}{was_txt})"
            )
            return title, f"{errand['name']}\n{spec.url}", False
        if spec.kind == "text_appears":
            return f'"{spec.keyword}" appeared on {label}', f"{errand['name']}\n{spec.url}", False
        if spec.kind == "text_disappears":
            return f'"{spec.keyword}" disappeared from {label}', f"{errand['name']}\n{spec.url}", False
        summary, used = self._summary(errand.get("last_text") or "", obs.text)
        return f"Page changed: {label}", f"{summary}\n{spec.url}", used

    def _record_failure(self, errand: dict, spec: ErrandSpec, reason: str, started: float) -> str:
        eid = errand["id"]
        failures = int(errand.get("consecutive_failures") or 0) + 1
        self.store.update_errand_state(eid, last_checked_at=_now(), last_status="error", consecutive_failures=failures)
        self.store.add_check(eid, "error", int((time.monotonic() - started) * 1000), None, False, False, reason[:1000])
        if failures == FAILURE_ALERT_AT:
            queue.push_notify(
                self.r,
                eid,
                f"Errand failing: {reason}"[:300],
                f"{errand['name']} failed {failures} checks in a row.\n{spec.url or spec.query or ''}",
            )
        CHECKS_TOTAL.labels(status="error").inc()
        return "error"

    # ------------------------------------------------------------ dry run

    def _dry_run_best_price(self, spec: ErrandSpec) -> dict:
        try:
            outcome = self.comparer().run(spec.query or spec.url or "")
        except Exception as exc:
            log.exception("best-price dry run crashed")
            return _dry_result(False, f"internal error ({type(exc).__name__})")
        best = outcome.best
        n = outcome.notes
        tally = f"{n.get('offers', 0)} offers, {n.get('failed', 0)} failed, {n.get('rejected', 0)} rejected"
        found = [o for o in outcome.offers if not o.get("error")]
        excerpt = "; ".join(f"{o['seller_name']} {format_money(o['price'], o.get('currency'))}" for o in found)
        if best is None:
            return _dry_result(False, f"No seller returned a matching price ({tally}).", excerpt=excerpt[:300])
        cond = bool(spec.threshold) and best["price"] < spec.threshold
        msg = f"Best price {best_summary(best)} ({tally})."
        if spec.every_minutes < 60:
            msg += " Each check visits several sellers; every 60 minutes or more is recommended."
        return _dry_result(True, msg, best["price"], cond, excerpt[:300])

    def dry_run(self, spec_data: dict) -> dict:
        try:
            spec = ErrandSpec.model_validate(spec_data)
        except Exception as exc:
            return {
                "ok": False,
                "value": None,
                "condition_true": None,
                "rendered": False,
                "selector": None,
                "excerpt": "",
                "message": f"invalid spec: {exc}"[:500],
            }
        if spec.kind in COMPARE_KINDS:
            return self._dry_run_best_price(spec)
        try:
            obs, _ = self.observe(spec, cheap_path=False)
        except (FetchError, CheckFailedError) as exc:
            return {
                "ok": False,
                "value": None,
                "condition_true": None,
                "rendered": False,
                "selector": None,
                "excerpt": "",
                "message": str(exc),
            }
        except Exception as exc:
            log.exception("dry run crashed")
            return {
                "ok": False,
                "value": None,
                "condition_true": None,
                "rendered": False,
                "selector": None,
                "excerpt": "",
                "message": f"internal error ({type(exc).__name__})",
            }
        value = obs.price.value if obs.price else None
        flat = " ".join(obs.lines)
        cond = condition_true(spec.kind, value=value, threshold=spec.threshold, keyword=spec.keyword, text=flat)
        parts: list[str] = list(obs.notes)
        selector = spec.selector if (spec.selector and obs.selector_matched) else None
        if obs.price:
            parts.append(f"Found {format_money(value, obs.price.currency)} via {obs.price.method}.")
            selector = (obs.price.learned or selector) if obs.price.method != "selector" else spec.selector
        if spec.selector and not obs.selector_matched:
            parts.append(f"Selector {spec.selector!r} matched nothing; used the whole page.")
        if obs.auto_rendered:
            parts.append("The page needs JavaScript — set render=true for faster, reliable checks.")
        if spec.kind == "page_changed":
            cond = False
            parts.append("Baseline captured; you'll be told when the page changes.")
        return {
            "ok": True,
            "value": value,
            "condition_true": cond,
            "rendered": obs.rendered,
            "selector": selector,
            "excerpt": _excerpt(flat, spec.keyword),
            "message": " ".join(parts),
        }


def _dry_result(ok: bool, message: str, value=None, cond=None, excerpt: str = "") -> dict:
    return {
        "ok": ok,
        "value": value,
        "condition_true": cond,
        "rendered": False,
        "selector": None,
        "excerpt": excerpt,
        "message": message,
    }


def _excerpt(flat: str, keyword: str | None, size: int = 300) -> str:
    if keyword:
        idx = flat.lower().find(keyword.lower())
        if idx >= 0:
            start = max(0, idx - size // 3)
            return flat[start : start + size]
    return flat[:size]


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- main loop


class Worker:
    def __init__(self, runner: CheckRunner, r):
        self.runner = runner
        self.r = r
        self.stopping = False

    def handle(self, job: dict) -> None:
        kind = job.get("type")
        if kind == "check" and job.get("errand_id"):
            status = self.runner.run_check(str(job["errand_id"]))
            log.info("check %s -> %s", job["errand_id"], status)
        elif kind == "compare" and job.get("compare_id"):
            max_pages = job.get("max_pages") if isinstance(job.get("max_pages"), int) else None
            status = self.runner.run_compare(str(job["compare_id"]), max_pages)
            log.info("comparison %s -> %s", job["compare_id"], status)
        elif kind == "dryrun" and isinstance(job.get("spec"), dict) and job.get("reply"):
            if queue.job_expired(job):
                log.info("skipping a dry run nobody is waiting for any more")
                return
            result = self.runner.dry_run(job["spec"])
            queue.push_reply(self.r, str(job["reply"]), result)
        else:
            log.warning("ignoring malformed job")

    def run_forever(self, poll_s: int = 5) -> None:
        while not self.stopping:
            try:
                job = queue.pop_job(self.r, timeout_s=poll_s)
            except Exception:
                log.warning("redis unavailable; retrying", exc_info=False)
                time.sleep(2)
                continue
            if job:
                try:
                    self.handle(job)
                except Exception:
                    log.exception("job failed")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from .db import PgStore
    from .fetch import Fetcher

    settings = load_settings()
    start_http_server(settings.worker_metrics_port)
    r = queue.connect(settings.redis_url)
    store = PgStore(settings.database_url, max_size=2)
    fetcher = Fetcher(settings.allowed_internal_hosts, r=r, host_gap_ms=settings.host_min_gap_ms)
    runner = CheckRunner(store, r, fetcher, LLMClient(settings, r), settings)
    worker = Worker(runner, r)

    def _stop(signum, _frame):
        log.info("signal %s: finishing current job then exiting", signum)
        worker.stopping = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("worker started (llm_mode=%s)", settings.llm_mode)
    try:
        worker.run_forever()
    finally:
        fetcher.close()
        store.close()
        log.info("worker stopped")


if __name__ == "__main__":
    main()
