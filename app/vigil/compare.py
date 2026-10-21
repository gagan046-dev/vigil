"""Price comparison: find the same product at several sellers and rank the offers.

Pipeline (run by the worker for a "compare" job, and for best_price_below watches):
  1. a URL query is fetched first: its product title becomes the search query and
     the page itself becomes one of the offers;
  2. the search provider returns product pages on allow-listed sellers;
  3. pages are fetched politely (SSRF guard, robots.txt, per-host gap) by a small
     thread pool; pages that need JavaScript are then rendered one at a time
     (Playwright's sync API is bound to one thread);
  4. offers are extracted (JSON-LD > meta > heuristics > LLM) and matched against
     the reference product; different variants are rejected, not ranked;
  5. offers are sorted in-stock first, then by price; the best one is marked.

CAPTCHAs, logins and bot walls are never bypassed: such sellers become error rows.
"""

from __future__ import annotations

import logging
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from .config import Settings
from .extract import format_money, page_lines, parse_html
from .fetch import FetchError, RobotsBlockedError, UnsafeURLError
from .matching import ACCEPT, Match, match_product
from .offers import OfferData, extract_offer, looks_like_bot_wall
from .search import SearchHit, Seller, seller_name, seller_of, sellers_for

log = logging.getLogger(__name__)

FETCH_WORKERS = 3
MAX_RENDERS = 3
MAX_LLM_CALLS = 3
PAGES_LIMIT = 20

LLM_OFFER_SYSTEM = (
    "You read the visible text of an online shop's product page. Reply with ONE JSON object: "
    '{"price": <current selling price as a number, no symbols or commas, or null>, '
    '"in_stock": <true, false or null>}.'
)
LLM_MATCH_SYSTEM = (
    "Decide whether two product listings are the SAME product (same model, capacity, size and "
    "variant; colour may differ). Reply with ONE JSON object: "
    '{"same": true or false, "reason": "<at most 12 words>"}.'
)

ProgressFn = Callable[[str, list[dict], dict], None]


def is_url(text: str) -> bool:
    return bool(re.match(r"^https?://\S+$", (text or "").strip(), re.IGNORECASE))


def clean_reference(title: str, max_words: int = 12) -> str:
    """'Apple iPhone 16 (128 GB) - Black | 6.1" display' -> 'Apple iPhone 16 (128 GB)'."""
    t = re.sub(r"\s+", " ", title or "").strip()
    for sep in (" | ", " – ", " — ", " - ", ": "):
        head = t.split(sep, 1)[0].strip()
        if head != t and len(head.split()) >= 2:
            t = head
    return " ".join(t.split()[:max_words])


def query_from_url(url: str) -> str:
    """Best guess at a product name from a URL slug: /apple-iphone-16-black/p/itm1 -> 'apple iphone 16 black'."""
    segments = [unquote(s) for s in urlsplit(url).path.split("/") if s]
    words = [s for s in segments if "-" in s or "_" in s]
    best = max(words, key=len, default="")
    return re.sub(r"[-_]+", " ", best).strip()


def classify_error(exc: Exception) -> str:
    if isinstance(exc, RobotsBlockedError):
        return "blocked by robots.txt"
    if isinstance(exc, UnsafeURLError):
        return "blocked: not a public address"
    msg = str(exc) or type(exc).__name__
    m = re.match(r"HTTP (\d{3})", msg)
    if m:
        code = int(m.group(1))
        if code in (401, 403, 405, 429, 503, 529):
            return f"HTTP {code} / bot protection"
        return f"HTTP {code}"
    if "timed out" in msg.lower():
        return "timeout"
    return msg[:120]


@dataclass
class _Page:
    hit: SearchHit
    offer: OfferData | None = None
    html: str = ""
    rendered: bool = False
    error: str | None = None
    llm_used: bool = False
    source_page: bool = False


@dataclass
class CompareOutcome:
    product: dict
    offers: list[dict]
    best: dict | None
    notes: dict = field(default_factory=dict)
    llm_used: bool = False


def _stock_rank(row: dict) -> int:
    return {True: 0, None: 1, False: 2}.get(row.get("in_stock"), 1)


def sort_offers(rows: list[dict]) -> list[dict]:
    """Valid offers: in stock first, then cheapest. Error rows last (by seller name)."""
    ok = [r for r in rows if not r.get("error") and r.get("price") is not None]
    bad = [r for r in rows if r.get("error") or r.get("price") is None]
    ok.sort(key=lambda r: (_stock_rank(r), r["price"], r.get("seller_name") or ""))
    bad.sort(key=lambda r: (r.get("seller_name") or "", r.get("url") or ""))
    return ok + bad


def pick_best(rows: list[dict]) -> dict | None:
    """Cheapest confirmed match that is not out of stock (in the most common currency)."""
    ok = [r for r in rows if not r.get("error") and r.get("price") is not None]
    if not ok:
        return None
    currency = Counter(r.get("currency") or "" for r in ok).most_common(1)[0][0]
    for verdicts in (("match",), ("match", "possible")):
        pool = [
            r
            for r in ok
            if r.get("match") in verdicts and r.get("in_stock") is not False and (r.get("currency") or "") == currency
        ]
        if pool:
            return min(pool, key=lambda r: (_stock_rank(r), r["price"]))
    return None


def best_summary(best: dict | None) -> str:
    if not best:
        return "no seller returned a price"
    return f"{format_money(best['price'], best.get('currency') or '₹')} at {best['seller_name']}"


class Comparer:
    def __init__(self, fetcher, search, llm, settings: Settings, sellers: list[Seller] | None = None):
        self.fetcher = fetcher
        self.search = search
        self.llm = llm
        self.settings = settings
        self.sellers = sellers if sellers is not None else sellers_for(settings)
        self._llm_calls = 0

    # ------------------------------------------------------------ helpers

    def _llm_ok(self) -> bool:
        return self.llm is not None and self._llm_calls < MAX_LLM_CALLS and self.llm.available()

    def _ask(self, system: str, user: str, purpose: str) -> dict | None:
        self._llm_calls += 1
        return self.llm.chat_json(system, user, purpose=purpose)

    @staticmethod
    def _prejudge(reference: str, hit: SearchHit) -> Match | None:
        """A rejecting Match when both the search title and the URL slug clearly describe another product.

        Lenient on purpose (search titles are often short or generic): only a hard rule
        (other model / capacity / variant / accessory) or almost no shared words.
        """
        if not reference:
            return None
        judged = [match_product(reference, t) for t in (hit.title, query_from_url(hit.url)) if t]
        if not judged or any(m.verdict != "rejected" or not (m.hard or m.score < 0.3) for m in judged):
            return None
        return max(judged, key=lambda m: m.score)

    def _fetch(self, page: _Page) -> _Page:
        try:
            fr = self.fetcher.fetch(page.hit.url)
        except FetchError as exc:
            page.error = classify_error(exc)
            return page
        except Exception as exc:  # one bad page must not sink the whole comparison
            log.warning("compare fetch crashed: %s", type(exc).__name__)
            page.error = f"internal error ({type(exc).__name__})"
            return page
        page.html, page.rendered = fr.html, fr.rendered
        page.offer = extract_offer(fr.html)
        return page

    def _render(self, page: _Page) -> None:
        try:
            fr = self.fetcher.fetch(page.hit.url, render=True)
        except FetchError as exc:
            page.error = f"needs JavaScript; rendering failed ({classify_error(exc)})"
            return
        offer = extract_offer(fr.html)
        page.html, page.rendered = fr.html, True
        if offer.price is not None or page.offer is None:
            page.offer = offer

    def _llm_offer(self, page: _Page) -> None:
        lines, _ = page_lines(parse_html(page.html))
        reply = self._ask(LLM_OFFER_SYSTEM, f"Page: {page.hit.url}\nText:\n{' '.join(lines)[:6000]}", "compare")
        page.llm_used = True
        try:
            price = float((reply or {}).get("price"))
        except (TypeError, ValueError):
            return
        if price > 0 and page.offer is not None:
            page.offer.price, page.offer.source = round(price, 2), "llm"
            page.offer.currency = page.offer.currency or "₹"
            stock = (reply or {}).get("in_stock")
            if isinstance(stock, bool) and page.offer.in_stock is None:
                page.offer.in_stock = stock

    def _row(self, page: _Page, reference: str) -> dict:
        s = page.hit.seller
        o = page.offer or OfferData()
        row = {
            "seller": s.key,
            "seller_name": s.name,
            "domain": s.domain,
            "url": page.hit.url,
            "title": o.title or page.hit.title or None,
            "price": o.price,
            "currency": o.currency or ("₹" if o.price is not None else None),
            "mrp": o.mrp,
            "discount_pct": None,
            "in_stock": o.in_stock,
            "image": o.image,
            "source": o.source,
            "rendered": page.rendered,
            "match_score": None,
            "match": None,
            "match_reason": None,
            "error": page.error,
            "best": False,
        }
        if o.mrp and o.price and o.mrp > o.price:
            row["discount_pct"] = round((o.mrp - o.price) / o.mrp * 100)
        if row["error"]:
            return row
        if o.price is None:
            row["error"] = "bot protection (CAPTCHA page)" if looks_like_bot_wall(page.html) else "no price found"
            return row
        if page.source_page:
            row.update(match_score=1.0, match="match", match_reason="the page you entered")
            return row
        m = match_product(reference, row["title"] or "")
        row.update(match_score=m.score, match=m.verdict, match_reason=m.reason)
        if m.verdict == "possible" and self._llm_ok():
            reply = self._ask(LLM_MATCH_SYSTEM, f"A: {reference}\nB: {row['title']}", "match") or {}
            page.llm_used = True
            if isinstance(reply.get("same"), bool):
                verdict = "match" if reply["same"] else "rejected"
                why = str(reply.get("reason") or "")[:100]
                row.update(match=verdict, match_reason=f"AI check: {why}" if why else row["match_reason"])
                if verdict == "match":
                    row["match_score"] = max(row["match_score"], ACCEPT)
        if row["match"] == "rejected":
            row["error"] = "different product"
        return row

    # ------------------------------------------------------------ pipeline

    def run(self, query: str, max_pages: int | None = None, progress: ProgressFn | None = None) -> CompareOutcome:
        started = time.monotonic()
        self._llm_calls = 0
        query = (query or "").strip()
        limit = max(1, min(PAGES_LIMIT, int(max_pages or self.settings.compare_max_pages)))
        notes: dict = {"messages": [], "provider": getattr(self.search, "name", "?"), "sellers": len(self.sellers)}
        product: dict = {"query": query, "title": None, "url": None, "image": None}

        def report(status: str, rows: list[dict]) -> None:
            if progress is not None:
                try:
                    progress(status, rows, dict(notes))
                except Exception:
                    log.warning("progress update failed", exc_info=False)

        pages: list[_Page] = []
        reference = query
        if is_url(query):
            product["url"] = query
            host = (urlsplit(query).hostname or "").lower().removeprefix("www.")
            seller = seller_of(query, self.sellers) or Seller(host, seller_name(host), host)
            source = self._fetch(_Page(SearchHit(query, "", seller), source_page=True))
            no_price = source.offer is None or source.offer.price is None
            if no_price and source.error is None and "<script" in source.html.lower():
                self._render(source)
            pages.append(source)
            if source.offer and source.offer.title:
                reference = clean_reference(source.offer.title)
                product["title"] = source.offer.title
            else:
                reference = query_from_url(query)
                notes["messages"].append(
                    f"Could not read the product page ({source.error or 'no title'}); searched for '{reference}'."
                )

        report("searching", [])
        hits: list[SearchHit] = []
        if reference:
            try:
                hits = self.search.search(reference, self.sellers)
            except Exception as exc:
                notes["messages"].append(f"Search failed ({type(exc).__name__}).")
        hits = [h for h in hits if h.url != product["url"]]
        # judge each hit by its search title / URL first: obviously different products are not fetched
        plausible, unlikely = [], {}
        for h in hits:
            m = self._prejudge(reference, h)
            if m is None:
                plausible.append(h)
            else:
                unlikely.setdefault(h.seller.key, (h, m))
        # one page per seller first, then second pages, until the budget is used up
        first, second, seen = [], [], set()
        for h in plausible:
            (second if h.seller.key in seen else first).append(h)
            seen.add(h.seller.key)
        budget = max(0, limit - len(pages))
        chosen = (first + second)[:budget]
        prejudged = [v for k, v in unlikely.items() if k not in seen]
        notes.update(reference=reference, searched=len(hits), total=len(chosen) + len(pages), fetched=len(pages))
        if not hits and reference:
            notes["messages"].append("The search returned no product pages on the allowed sellers.")

        report("fetching", [])
        todo = [_Page(h) for h in chosen]
        rows: list[dict] = []
        if todo:
            with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
                for fut in as_completed([pool.submit(self._fetch, p) for p in todo]):
                    fut.result()
                    notes["fetched"] += 1
                    report("fetching", rows)
        pages.extend(todo)

        renders = 0
        for page in pages:
            if page.source_page or page.error or (page.offer and page.offer.price is not None):
                continue
            if renders < MAX_RENDERS and "<script" in page.html.lower() and not looks_like_bot_wall(page.html):
                renders += 1
                self._render(page)
        for page in pages:
            no_price = page.offer is not None and page.offer.price is None
            if not page.error and no_price and not looks_like_bot_wall(page.html) and self._llm_ok():
                self._llm_offer(page)

        rows = [self._row(p, reference) for p in pages]
        for hit, m in prejudged:
            row = self._row(_Page(hit, error="different product"), reference)
            row.update(match_score=m.score, match="rejected", match_reason=f"search result: {m.reason}"[:200])
            rows.append(row)
        covered = {p.hit.seller.key for p in pages} | {h.seller.key for h, _ in prejudged}
        hit_sellers = {h.seller.key for h in hits}
        for s in self.sellers:
            if s.key not in covered:
                rows.append(
                    {
                        "seller": s.key,
                        "seller_name": s.name,
                        "domain": s.domain,
                        "url": None,
                        "title": None,
                        "price": None,
                        "currency": None,
                        "mrp": None,
                        "discount_pct": None,
                        "in_stock": None,
                        "image": None,
                        "source": None,
                        "rendered": False,
                        "match_score": None,
                        "match": None,
                        "match_reason": None,
                        "error": "skipped (page limit)" if s.key in hit_sellers else "not found in search results",
                        "best": False,
                    }
                )
        rows = sort_offers(rows)
        best = pick_best(rows)
        if best is not None:
            best["best"] = True
        if not product["title"]:
            matches = [r for r in rows if r.get("match") == "match" and r.get("title")]
            product["title"] = (
                clean_reference(max(matches, key=lambda r: r["match_score"])["title"]) if matches else query
            )
        product["image"] = next((r["image"] for r in ([best] if best else []) + rows if r and r.get("image")), None)
        valid = [r for r in rows if not r.get("error")]
        notes.update(
            offers=len(valid),
            rejected=sum(1 for r in rows if r.get("match") == "rejected"),
            failed=sum(1 for r in rows if r.get("error") and r.get("match") != "rejected"),
            llm_calls=self._llm_calls,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        return CompareOutcome(product, rows, dict(best) if best else None, notes, self._llm_calls > 0)
