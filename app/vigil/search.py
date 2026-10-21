"""Find seller product pages for a product name.

Providers (env SEARCH_PROVIDER):
  demo    - offline: the fictional sellers in demo_shop.py (dev and tests)
  searxng - a self-hosted SearXNG metasearch instance (JSON API, SEARXNG_URL)

A Seller is one shop on the allow-list. Real sellers are identified by domain
(flipkart.com, amazon.in, ...); the demo sellers share one host and are told
apart by their path prefix (/sellers/<key>/).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import Settings

log = logging.getLogger(__name__)

SEARCH_TIMEOUT_S = 15.0

# display names for well-known Indian sellers; anything else is derived from the domain
_NAMES = {
    "flipkart.com": "Flipkart",
    "amazon.in": "Amazon.in",
    "myntra.com": "Myntra",
    "croma.com": "Croma",
    "reliancedigital.in": "Reliance Digital",
    "vijaysales.com": "Vijay Sales",
    "tatacliq.com": "Tata CLiQ",
    "ajio.com": "AJIO",
}

# fictional demo sellers: key -> (display name, product path)
DEMO_SELLERS = {
    "pixelmart": ("PixelMart", "/sellers/pixelmart/aurora-15"),
    "bazaar99": ("Bazaar99", "/sellers/bazaar99/aurora-15"),
    "northwind": ("Northwind Electronics", "/sellers/northwind/aurora-15"),
    "bytebay": ("ByteBay", "/sellers/bytebay/aurora-15-pro"),
    "quietcart": ("QuietCart", "/sellers/quietcart/aurora-15"),
}
_DEMO_TITLES = {
    "pixelmart": "Aurora 15 (RTX 5070) Gaming Laptop - PixelMart",
    "bazaar99": "Aurora 15 RTX 5070 15.6 inch Gaming Laptop | Bazaar99",
    "northwind": "Aurora 15 Gaming Laptop, RTX 5070 - Northwind Electronics",
    "bytebay": "Aurora 15 Pro RTX 5080 Gaming Laptop - ByteBay",
    "quietcart": "Aurora 15 RTX 5070 - QuietCart",
}


@dataclass(frozen=True)
class Seller:
    key: str
    name: str
    domain: str
    path_prefix: str = ""

    def owns(self, url: str) -> bool:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if not (host == self.domain or host.endswith("." + self.domain)):
            return False
        return not self.path_prefix or parts.path.startswith(self.path_prefix)


@dataclass(frozen=True)
class SearchHit:
    url: str
    title: str
    seller: Seller


def seller_name(domain: str) -> str:
    if domain in _NAMES:
        return _NAMES[domain]
    return domain.split(".")[0].capitalize()


def sellers_for(settings: Settings) -> list[Seller]:
    if settings.search_provider == "demo":
        host = (urlsplit(settings.demo_shop_url).hostname or "vigil-demo-shop").lower()
        return [Seller(k, name, host, f"/sellers/{k}/") for k, (name, _) in DEMO_SELLERS.items()]
    out = []
    for d in settings.compare_sellers:
        d = d.lower().removeprefix("www.").strip(".")
        if d:
            out.append(Seller(d, seller_name(d), d))
    return out


def seller_of(url: str, sellers: list[Seller]) -> Seller | None:
    return next((s for s in sellers if s.owns(url)), None)


def _is_listing_page(url: str) -> bool:
    """Skip home/search/category pages: we want product pages."""
    parts = urlsplit(url)
    path = parts.path.lower().rstrip("/")
    if not path:
        return True
    listing = ("/search", "/s/", "/browse", "/category", "/c/", "/collection", "/l/", "/b/", "/brand", "/stores/")
    if any(seg in path + "/" for seg in listing):
        return True
    return path in ("/s", "/search", "/shop")


def pick_hits(results: list[tuple[str, str]], sellers: list[Seller], per_seller: int = 2) -> list[SearchHit]:
    """Keep the top per_seller product-page results for each allowed seller (in result order)."""
    counts: dict[str, int] = {}
    seen: set[str] = set()
    out: list[SearchHit] = []
    for url, title in results:
        if not isinstance(url, str) or not url.startswith(("http://", "https://")) or url in seen:
            continue
        seller = seller_of(url, sellers)
        if seller is None or _is_listing_page(url) or counts.get(seller.key, 0) >= per_seller:
            continue
        seen.add(url)
        counts[seller.key] = counts.get(seller.key, 0) + 1
        out.append(SearchHit(url, (title or "")[:300], seller))
    return out


class DemoSearch:
    name = "demo"

    def __init__(self, settings: Settings):
        self.base = settings.demo_shop_url.rstrip("/")
        self.sellers = sellers_for(settings)

    def search(self, query: str, sellers: list[Seller]) -> list[SearchHit]:
        if "aurora" not in (query or "").lower():
            return []
        results = [(self.base + path, _DEMO_TITLES[key]) for key, (_, path) in DEMO_SELLERS.items()]
        return pick_hits(results, sellers)


class SearxngSearch:
    """SearXNG JSON API. One broad query, then site: queries for sellers it missed."""

    name = "searxng"

    def __init__(self, settings: Settings, http: httpx.Client | None = None, workers: int = 3):
        self.base = settings.searxng_url.rstrip("/")
        self.http = http or httpx.Client(
            timeout=SEARCH_TIMEOUT_S, headers={"Accept": "application/json", "User-Agent": "VigilBot/0.1"}
        )
        self.workers = workers

    def _query(self, q: str) -> list[tuple[str, str]]:
        try:
            resp = self.http.get(f"{self.base}/search", params={"q": q, "format": "json", "safesearch": "1"})
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            log.warning("searxng query failed: %s", type(exc).__name__)
            return []
        results = data.get("results") if isinstance(data, dict) else None
        out = []
        for item in results if isinstance(results, list) else []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                out.append((item["url"], str(item.get("title") or "")))
        return out

    def search(self, query: str, sellers: list[Seller]) -> list[SearchHit]:
        results = self._query(query)
        found = {h.seller.key for h in pick_hits(results, sellers)}
        missing = [s for s in sellers if s.key not in found]
        if missing:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                for extra in pool.map(lambda s: self._query(f"site:{s.domain} {query}"), missing):
                    results.extend(extra)
        return pick_hits(results, sellers)

    def ping(self) -> bool:
        try:
            return self.http.get(f"{self.base}/healthz", timeout=5.0).status_code == 200
        except Exception:
            return False


def make_search(settings: Settings):
    if settings.search_provider == "searxng":
        return SearxngSearch(settings)
    return DemoSearch(settings)
