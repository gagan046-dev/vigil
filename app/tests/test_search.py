import httpx
import respx

from vigil.config import Settings
from vigil.search import DemoSearch, SearxngSearch, Seller, make_search, pick_hits, sellers_for

SX = "http://searxng.test:8080"


def sx_settings(**kw):
    return Settings(search_provider="searxng", searxng_url=SX, compare_sellers=("flipkart.com", "amazon.in"), **kw)


def results(*urls):
    return {"results": [{"url": u, "title": f"title of {u}"} for u in urls]}


def test_sellers_for_real_and_demo():
    real = sellers_for(sx_settings())
    assert [(s.key, s.name, s.domain) for s in real] == [
        ("flipkart.com", "Flipkart", "flipkart.com"),
        ("amazon.in", "Amazon.in", "amazon.in"),
    ]
    demo = sellers_for(Settings(search_provider="demo"))
    assert {s.key for s in demo} == {"pixelmart", "bazaar99", "northwind", "bytebay", "quietcart"}
    assert all(s.domain == "vigil-demo-shop" for s in demo)


def test_seller_owns_subdomains_and_paths():
    s = Seller("amazon.in", "Amazon.in", "amazon.in")
    assert s.owns("https://www.amazon.in/dp/B0X") and not s.owns("https://amazon.in.evil.com/dp/B0X")
    demo = Seller("pixelmart", "PixelMart", "vigil-demo-shop", "/sellers/pixelmart/")
    assert demo.owns("http://vigil-demo-shop:8000/sellers/pixelmart/x")
    assert not demo.owns("http://vigil-demo-shop:8000/sellers/bazaar99/x")


def test_pick_hits_filters_domains_listing_pages_and_limits_per_seller():
    sellers = sellers_for(sx_settings())
    hits = pick_hits(
        [
            ("https://www.flipkart.com/search?q=iphone", "search page"),
            ("https://www.flipkart.com/apple-iphone-16/p/itm1", "a"),
            ("https://www.flipkart.com/apple-iphone-16/p/itm1", "dup"),
            ("https://www.flipkart.com/apple-iphone-16-b/p/itm2", "b"),
            ("https://www.flipkart.com/apple-iphone-16-c/p/itm3", "c"),
            ("https://www.amazon.in/s?k=iphone", "amazon search"),
            ("https://www.amazon.in/Apple-iPhone-16/dp/B0DGJ", "amz"),
            ("https://www.example.com/apple-iphone-16", "not allowed"),
            ("ftp://www.amazon.in/x", "bad scheme"),
        ],
        sellers,
    )
    assert [h.url for h in hits] == [
        "https://www.flipkart.com/apple-iphone-16/p/itm1",
        "https://www.flipkart.com/apple-iphone-16-b/p/itm2",
        "https://www.amazon.in/Apple-iPhone-16/dp/B0DGJ",
    ]


@respx.mock
def test_searxng_broad_query_then_site_queries_for_missing_sellers():
    broad = respx.get(f"{SX}/search", params={"q": "Apple iPhone 16 128GB"}).mock(
        return_value=httpx.Response(200, json=results("https://www.flipkart.com/apple-iphone-16/p/itm1"))
    )
    site = respx.get(f"{SX}/search", params={"q": "site:amazon.in Apple iPhone 16 128GB"}).mock(
        return_value=httpx.Response(200, json=results("https://www.amazon.in/Apple-iPhone-16/dp/B0DGJ"))
    )
    search = SearxngSearch(sx_settings())
    hits = search.search("Apple iPhone 16 128GB", sellers_for(sx_settings()))
    assert {h.seller.key for h in hits} == {"flipkart.com", "amazon.in"}
    assert broad.called and site.call_count == 1
    assert broad.calls[0].request.url.params["format"] == "json"


@respx.mock
def test_searxng_errors_are_empty_results():
    respx.get(f"{SX}/search").mock(return_value=httpx.Response(429, text="Too Many Requests"))
    assert SearxngSearch(sx_settings()).search("x", sellers_for(sx_settings())) == []
    respx.get(f"{SX}/search").mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    assert SearxngSearch(sx_settings()).search("x", sellers_for(sx_settings())) == []
    respx.get(f"{SX}/healthz").mock(return_value=httpx.Response(200, text="OK"))
    assert SearxngSearch(sx_settings()).ping() is True


def test_demo_search_only_knows_the_aurora():
    s = Settings(search_provider="demo")
    demo = make_search(s)
    assert isinstance(demo, DemoSearch)
    hits = demo.search("Aurora 15 (RTX 5070)", sellers_for(s))
    assert len(hits) == 5 and all(h.url.startswith("http://vigil-demo-shop:8000/sellers/") for h in hits)
    assert demo.search("iPhone 16", sellers_for(s)) == []
    assert isinstance(make_search(sx_settings()), SearxngSearch)


def test_listing_pages_are_skipped():
    from vigil.search import _is_listing_page

    for url in (
        "https://www.reliancedigital.in/collection/iphone-16-19-02",
        "https://www.croma.com/l/apple-iphone-16-available-0asz00a.html",
        "https://www.ajio.com/b/apple",
        "https://www.flipkart.com/",
        "https://www.amazon.in/s?k=iphone",
    ):
        assert _is_listing_page(url), url
    for url in (
        "https://www.croma.com/apple-iphone-16-plus-128gb-white-/p/309708",
        "https://www.amazon.in/iPhone-16-128-GB-Control/dp/B0DGJHBX5Y",
        "https://www.vijaysales.com/p/P261219/261223/apple-iphone-16-128gb-storage-white",
    ):
        assert not _is_listing_page(url), url
