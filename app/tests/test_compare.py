"""The comparison pipeline end to end: the real Fetcher (robots.txt, HTTP) against the demo sellers."""

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeFetcher, FakeLLM
from vigil import demo_shop
from vigil.compare import Comparer, classify_error, clean_reference, query_from_url, sort_offers
from vigil.config import Settings
from vigil.fetch import Fetcher, FetchError, FetchResult, RobotsBlockedError
from vigil.search import DemoSearch, SearchHit, Seller, sellers_for

SHOP = "http://vigil-demo-shop:8000"
DEMO = Settings(env="test", search_provider="demo", allowed_internal_hosts=("vigil-demo-shop",), host_min_gap_ms=0)


class DemoFetcher(Fetcher):
    """Real safety/robots/HTTP path over the demo ASGI app; 'rendering' runs the page's JavaScript by hand."""

    def __init__(self, client: TestClient):
        super().__init__(("vigil-demo-shop",), r=None, host_gap_ms=0, http=client)
        self.client = client
        self.renders: list[str] = []

    def _fetch_browser(self, url):
        self.renders.append(url)
        html = self.client.get(url).text
        if "/sellers/northwind/" in url:
            p = self.client.get("/sellers/northwind/api").json()
            html = html.replace("id='nw-price'>Loading…", f"id='nw-price'>{p['price_text']}")
            html = html.replace("id='nw-stock'>Loading…", f"id='nw-stock'>{p['stock']}")
        return FetchResult(html, 200, url, rendered=True)


@pytest.fixture
def shop():
    demo_shop.reset_state()
    client = TestClient(demo_shop.app, base_url=SHOP)
    yield client
    demo_shop.reset_state()


def comparer(shop, llm=None):
    fetcher = DemoFetcher(shop)
    return Comparer(fetcher, DemoSearch(DEMO), llm, DEMO), fetcher


def by_seller(outcome):
    return {o["seller"]: o for o in outcome.offers}


def test_demo_comparison_end_to_end(shop):
    c, fetcher = comparer(shop)
    seen: list[str] = []
    out = c.run("Aurora 15 RTX 5070", progress=lambda status, rows, notes: seen.append(status))
    rows = by_seller(out)

    assert seen[0] == "searching" and "fetching" in seen
    assert out.best["seller"] == "bazaar99" and out.best["price"] == 124499.0 and out.best["best"] is True
    assert rows["bazaar99"]["source"] == "meta" and rows["bazaar99"]["mrp"] == 139990.0
    assert rows["pixelmart"]["source"] == "jsonld" and rows["pixelmart"]["discount_pct"] == 13
    assert rows["northwind"]["price"] == 131000.0 and rows["northwind"]["rendered"] is True
    assert fetcher.renders == [f"{SHOP}/sellers/northwind/aurora-15"]  # only the JavaScript page was rendered
    assert rows["bytebay"]["match"] == "rejected" and rows["bytebay"]["error"] == "different product"
    assert rows["quietcart"]["error"] == "blocked by robots.txt" and rows["quietcart"]["price"] is None
    # ranked: valid offers by price, then error rows
    assert [o["seller"] for o in out.offers][:3] == ["bazaar99", "pixelmart", "northwind"]
    assert all(o["error"] for o in out.offers[3:])
    assert out.notes["offers"] == 3 and out.notes["rejected"] == 1 and out.notes["failed"] == 1
    assert out.product["title"].startswith("Aurora 15")


def test_admin_price_change_moves_the_best_offer(shop):
    shop.post("/admin/sellers/pixelmart", json={"price": 119990})
    shop.post("/admin/sellers/bazaar99", json={"in_stock": False})
    out = comparer(shop)[0].run("aurora 15")
    assert out.best["seller"] == "pixelmart" and out.best["price"] == 119990.0
    assert by_seller(out)["bazaar99"]["in_stock"] is False
    assert out.offers[-1]["error"]  # error rows stay last even with an out-of-stock offer present
    assert shop.post("/admin/sellers/nope", json={"price": 1}).status_code == 404


def test_url_query_uses_the_page_title_and_includes_the_page(shop):
    url = f"{SHOP}/sellers/pixelmart/aurora-15"
    out = comparer(shop)[0].run(url)
    rows = by_seller(out)
    assert out.product["title"] == "Aurora 15 (RTX 5070) Gaming Laptop" and out.product["url"] == url
    assert rows["pixelmart"]["match_reason"] == "the page you entered"
    assert sum(1 for o in out.offers if o["seller"] == "pixelmart") == 1  # not fetched twice
    assert out.best["seller"] == "bazaar99"


def test_unknown_product_gives_not_found_rows(shop):
    out = comparer(shop)[0].run("Kettle 1.5L")
    assert out.best is None and all(o["error"] == "not found in search results" for o in out.offers)
    assert any("no product pages" in m for m in out.notes["messages"])


def test_page_limit_marks_skipped_sellers(shop):
    out = comparer(shop)[0].run("aurora 15", max_pages=2)
    errors = [o["error"] for o in out.offers]
    # ByteBay's search result already says "Pro RTX 5080": rejected without spending a page on it
    assert errors.count("skipped (page limit)") == 2 and out.notes["total"] == 2
    assert by_seller(out)["bytebay"]["match_reason"].startswith("search result: different variant: pro")


def test_obviously_different_search_hits_are_not_fetched():
    sellers = SELLERS[:3]
    found = [
        SearchHit("https://alpha.test/apple-iphone-16-128gb/p/1", "Apple iPhone 16 (128 GB) - Black", sellers[0]),
        SearchHit("https://beta.test/sandisk-ixpand-128gb/p/2", "SanDisk iXpand Mini 128GB Flash Drive", sellers[1]),
        SearchHit("https://beta.test/apple-iphone-16-white/p/3", "Apple iPhone 16 White", sellers[1]),
        SearchHit("https://gamma.test/apple-iphone-16-plus-128gb/p/4", "Apple iPhone 16 Plus 128GB", sellers[2]),
    ]
    f = FakeFetcher(
        {
            (found[0].url, False): ld_page("Apple iPhone 16 (128 GB) - Black", 76999),
            (found[2].url, False): ld_page("Apple iPhone 16 (White, 128 GB)", 74999),
        }
    )
    out = Comparer(f, FixedSearch(found), None, DEMO, sellers=sellers).run("Apple iPhone 16 128GB")
    assert sorted(u for u, _ in f.calls) == sorted([found[0].url, found[2].url])  # pendrive and Plus skipped
    rows = by_seller(out)
    assert out.best["seller"] == "beta" and rows["gamma"]["error"] == "different product"
    assert "plus" in rows["gamma"]["match_reason"]


# ---------------------------------------------------------------- failure rows with fakes

SELLERS = [Seller(k, k.title(), f"{k}.test") for k in ("alpha", "beta", "gamma", "delta", "eps")]


class FixedSearch:
    name = "fixed"

    def __init__(self, hits):
        self.hits = hits

    def search(self, query, sellers):
        return self.hits


def hits():
    return [SearchHit(f"https://{s.domain}/p/widget-9", "Widget 9", s) for s in SELLERS]


def ld_page(name, price):
    return (
        "<html><head><script type='application/ld+json'>"
        f'{{"@type":"Product","name":"{name}","offers":{{"price":{price},"priceCurrency":"INR"}}}}'
        "</script></head><body><h1>{name}</h1></body></html>"
    )


def test_error_classification_rows():
    f = FakeFetcher(
        {
            ("https://alpha.test/p/widget-9", False): ld_page("Widget 9", 500),
            ("https://beta.test/p/widget-9", False): FetchError("HTTP 403 from beta.test"),
            ("https://gamma.test/p/widget-9", False): FetchError("timed out fetching the page"),
            ("https://delta.test/p/widget-9", False): "<html><body><p>Please solve this captcha</p></body></html>",
            ("https://eps.test/p/widget-9", False): "<html><body><h1>Widget 9</h1><p>no price</p></body></html>",
        }
    )
    out = Comparer(f, FixedSearch(hits()), None, DEMO, sellers=SELLERS).run("Widget 9")
    rows = by_seller(out)
    assert rows["alpha"]["price"] == 500.0 and out.best["seller"] == "alpha"
    assert rows["beta"]["error"] == "HTTP 403 / bot protection"
    assert rows["gamma"]["error"] == "timeout"
    assert rows["delta"]["error"] == "bot protection (CAPTCHA page)"
    assert rows["eps"]["error"] == "no price found"
    assert not any(render for _, render in f.calls)  # no <script>: nothing to render


def test_llm_resolves_possible_matches_and_reads_prices():
    pages = {
        ("https://alpha.test/p/widget-9", False): ld_page("Widget 9 Deluxe Edition Graphite Large", 700),
        ("https://beta.test/p/widget-9", False): "<html><body><h1>Widget 9</h1><p>seven hundred</p></body></html>",
    }
    f = FakeFetcher(pages)
    llm = FakeLLM([{"price": 650, "in_stock": True}, {"same": True, "reason": "same widget"}])
    out = Comparer(f, FixedSearch(hits()[:2]), llm, DEMO, sellers=SELLERS[:2]).run("Widget 9 Graphite Small Gift")
    rows = by_seller(out)
    assert [p for p, _ in llm.calls] == ["compare", "match"]
    assert rows["beta"]["source"] == "llm" and rows["beta"]["price"] == 650.0
    assert rows["alpha"]["match"] == "match" and rows["alpha"]["match_reason"].startswith("AI check")
    assert out.llm_used is True


def test_possible_match_without_llm_is_kept_but_not_preferred():
    rows = [
        {"seller": "a", "seller_name": "A", "price": 90.0, "in_stock": True, "match": "possible", "error": None},
        {"seller": "b", "seller_name": "B", "price": 100.0, "in_stock": None, "match": "match", "error": None},
        {"seller": "c", "seller_name": "C", "price": 80.0, "in_stock": False, "match": "match", "error": None},
        {"seller": "d", "seller_name": "D", "price": None, "in_stock": None, "match": None, "error": "timeout"},
    ]
    from vigil.compare import pick_best

    ordered = sort_offers(rows)
    assert [r["seller"] for r in ordered] == ["a", "b", "c", "d"]  # in stock first, then unknown, then out
    assert pick_best(ordered)["seller"] == "b"


def test_helpers():
    assert clean_reference('Apple iPhone 16 (128 GB) - Black | 6.1" display') == "Apple iPhone 16 (128 GB)"
    assert query_from_url("https://www.flipkart.com/apple-iphone-16-black-128-gb/p/itm7c0281") == (
        "apple iphone 16 black 128 gb"
    )
    assert classify_error(RobotsBlockedError("x")) == "blocked by robots.txt"
    assert classify_error(FetchError("HTTP 404 from x")) == "HTTP 404"
    assert sellers_for(DEMO)[0].key == "pixelmart"
