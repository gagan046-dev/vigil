import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeFetcher, FakeLLM, MemoryStore
from vigil import demo_shop, queue
from vigil.fetch import FetchError
from vigil.worker import CheckRunner, Worker

SHOP = "http://vigil-demo-shop:8000"


@pytest.fixture
def shop():
    demo_shop.reset_state()
    client = TestClient(demo_shop.app)
    yield client
    demo_shop.reset_state()


def static_html(shop):
    return shop.get("/static").text


def rendered_index(shop):
    """What a browser would see on / after the JavaScript ran."""
    p = shop.get("/api/product").json()
    html = shop.get("/").text
    html = html.replace(
        "<span class='price-now' id='price'>Loading…</span>",
        f"<span class='price-now' id='price'>{p['price_text']}</span>",
    )
    return html.replace(
        "<p class='stock' id='stock'>Loading…</p>",
        f"<p class='stock' id='stock'>{'In stock' if p['in_stock'] else 'Out of stock'}</p>",
    )


def make(r, settings, fetcher, llm=None):
    store = MemoryStore()
    return store, CheckRunner(store, r, fetcher, llm, settings)


def price_spec(url=f"{SHOP}/static", threshold=120000, **kw):
    return {"name": "Aurora", "url": url, "kind": "price_below", "threshold": threshold, "every_minutes": 5, **kw}


def notifications(r):
    return [json.loads(x) for x in r.lrange(queue.NOTIFY, 0, -1)]


def test_price_drop_full_cycle_with_cheap_path_and_rearm(r, settings, shop):
    fetcher = FakeFetcher()
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("Aurora", price_spec(), None)
    url = f"{SHOP}/static"

    fetcher.set(url, static_html(shop))
    assert runner.run_check(e["id"]) == "baseline"
    st = store.get_errand(e["id"])
    assert st["last_value"] == 134990 and st["selector_learned"] == "span.price-now"
    assert st["condition_met"] is False

    # identical page -> cheap path, no LLM, value reused
    assert runner.run_check(e["id"]) == "unchanged"
    assert store.checks[-1]["value"] == 134990

    shop.post("/admin/price", json={"value": 114990})
    fetcher.set(url, static_html(shop))
    assert runner.run_check(e["id"]) == "triggered"
    [n] = notifications(r)
    assert n["title"] == "Price drop: Aurora 15 (RTX 5070) is ₹1,14,990 (target < ₹1,20,000, was ₹1,34,990)"
    assert n["errand_id"] == e["id"]

    shop.post("/admin/price", json={"value": 110000})  # still below: no repeat
    fetcher.set(url, static_html(shop))
    assert runner.run_check(e["id"]) == "changed"
    assert len(notifications(r)) == 1

    shop.post("/admin/price", json={"value": 130000})  # re-arm
    fetcher.set(url, static_html(shop))
    runner.run_check(e["id"])
    shop.post("/admin/price", json={"value": 100000})
    fetcher.set(url, static_html(shop))
    assert runner.run_check(e["id"]) == "triggered"
    assert len(notifications(r)) == 2
    assert all(c["llm_used"] is False for c in store.checks)


def test_self_healing_selector_layout_1_to_2(r, settings, shop):
    fetcher = FakeFetcher()
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("Aurora", price_spec(), None)
    url = f"{SHOP}/static"
    fetcher.set(url, static_html(shop))
    runner.run_check(e["id"])
    assert store.get_errand(e["id"])["selector_learned"] == "span.price-now"

    shop.post("/admin/layout", json={"layout": 2})
    fetcher.set(url, static_html(shop))
    runner.run_check(e["id"])
    st = store.get_errand(e["id"])
    assert st["selector_learned"] == "div.pdp-amount"
    assert st["last_value"] == 134990
    assert "selector healed: span.price-now -> div.pdp-amount" in store.checks[-1]["message"]


def test_user_selector_used_when_it_works(r, settings, shop):
    fetcher = FakeFetcher({(f"{SHOP}/static", False): static_html(shop)})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("A", price_spec(selector=".price-now"), None)
    runner.run_check(e["id"])
    assert "via selector" in store.checks[-1]["message"]
    assert store.get_errand(e["id"])["selector_learned"] is None


def test_auto_render_fallback_for_js_price(r, settings, shop):
    url = f"{SHOP}/"
    fetcher = FakeFetcher({(url, False): shop.get("/").text, (url, True): rendered_index(shop)})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("A", price_spec(url=url), None)
    assert runner.run_check(e["id"]) == "baseline"
    assert fetcher.calls == [(url, False), (url, True)]
    c = store.checks[-1]
    assert c["rendered"] is True and c["value"] == 134990
    assert "rendered in a browser" in c["message"]
    assert store.get_errand(e["id"])["selector_learned"] == "span#price"


def test_dry_run_suggests_render_and_has_no_side_effects(r, settings, shop):
    url = f"{SHOP}/"
    fetcher = FakeFetcher({(url, False): shop.get("/").text, (url, True): rendered_index(shop)})
    store, runner = make(r, settings, fetcher)
    res = runner.dry_run(price_spec(url=url, threshold=150000))
    assert res["ok"] is True and res["value"] == 134990 and res["condition_true"] is True
    assert res["rendered"] is True and res["selector"] == "span#price"
    assert "render=true" in res["message"]
    assert store.checks == [] and notifications(r) == []


def test_dry_run_errors_are_reported(r, settings):
    fetcher = FakeFetcher({("https://x.test/", False): FetchError("blocked by robots.txt")})
    _, runner = make(r, settings, fetcher)
    res = runner.dry_run({"name": "x", "url": "https://x.test/", "kind": "page_changed"})
    assert res["ok"] is False and res["message"] == "blocked by robots.txt"
    bad = runner.dry_run({"name": "x", "url": "https://x.test/", "kind": "price_below"})
    assert bad["ok"] is False and "invalid spec" in bad["message"]


def test_text_appears_stock_with_render(r, settings, shop):
    url = f"{SHOP}/"
    fetcher = FakeFetcher({(url, False): shop.get("/").text, (url, True): rendered_index(shop)})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand(
        "Stock", {"name": "Stock", "url": url, "kind": "text_appears", "keyword": "In stock", "every_minutes": 5}, None
    )
    assert runner.run_check(e["id"]) == "baseline"
    assert store.checks[-1]["rendered"] is True
    shop.post("/admin/stock", json={"in_stock": True})
    fetcher.set(url, rendered_index(shop), render=True)
    assert runner.run_check(e["id"]) == "triggered"
    assert notifications(r)[0]["title"] == '"In stock" appeared on Aurora 15 (RTX 5070)'


def test_page_changed_baseline_then_summary(r, settings, shop):
    url = f"{SHOP}/notices"
    fetcher = FakeFetcher({(url, False): shop.get("/notices").text})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand(
        "Board", {"name": "Board", "url": url, "kind": "page_changed", "every_minutes": 5, "selector": "#notices"}, None
    )
    assert runner.run_check(e["id"]) == "baseline"
    assert notifications(r) == []
    shop.post("/admin/notice", json={"text": "Exam on Monday"})
    fetcher.set(url, shop.get("/notices").text)
    assert runner.run_check(e["id"]) == "triggered"
    n = notifications(r)[0]
    assert n["title"] == "Page changed: Notice board"
    assert "+1/-0 lines" in n["body"] and "Exam on Monday" in n["body"]


def test_page_changed_llm_summary(r, settings, shop):
    url = f"{SHOP}/notices"
    fetcher = FakeFetcher({(url, False): shop.get("/notices").text})
    llm = FakeLLM(replies=[{"summary": "A new exam notice was posted."}])
    store, runner = make(r, settings, fetcher, llm)
    e = store.create_errand(
        "Board", {"name": "Board", "url": url, "kind": "page_changed", "every_minutes": 5, "selector": "#notices"}, None
    )
    runner.run_check(e["id"])
    shop.post("/admin/notice", json={"text": "Exam on Monday"})
    fetcher.set(url, shop.get("/notices").text)
    runner.run_check(e["id"])
    assert notifications(r)[0]["body"].startswith("A new exam notice was posted.")
    assert store.checks[-1]["llm_used"] is True
    assert [c[0] for c in llm.calls] == ["summary"]


def test_llm_extraction_when_heuristics_fail(r, settings):
    url = "https://odd.example/item"
    html = "<html><body><h1>Odd shop</h1><div class='x'>Our offer today: 95 thousand only</div></body></html>"
    fetcher = FakeFetcher({(url, False): html, (url, True): html})
    llm = FakeLLM(replies=[{"value": 95000, "snippet": "95 thousand"}])
    store, runner = make(r, settings, fetcher, llm)
    e = store.create_errand("Odd", price_spec(url=url), None)
    assert runner.run_check(e["id"]) == "triggered"
    c = store.checks[-1]
    assert c["llm_used"] is True and c["value"] == 95000
    assert store.get_errand(e["id"])["selector_learned"] == "div.x"


def test_failures_notify_once_and_reset(r, settings, shop):
    url = f"{SHOP}/static"
    fetcher = FakeFetcher({(url, False): FetchError("timed out fetching the page")})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("A", price_spec(), None)
    for _ in range(5):
        assert runner.run_check(e["id"]) == "error"
    assert store.get_errand(e["id"])["consecutive_failures"] == 5
    ns = notifications(r)
    assert len(ns) == 1 and ns[0]["title"] == "Errand failing: timed out fetching the page"
    fetcher.set(url, static_html(shop))
    runner.run_check(e["id"])
    assert store.get_errand(e["id"])["consecutive_failures"] == 0


def test_price_not_found_is_an_error(r, settings):
    url = "https://plain.example/"
    html = "<body><p>nothing to see</p></body>"
    fetcher = FakeFetcher({(url, False): html})
    store, runner = make(r, settings, fetcher, FakeLLM(available=False))
    e = store.create_errand("A", price_spec(url=url), None)
    assert runner.run_check(e["id"]) == "error"
    assert store.checks[-1]["message"] == "price not found on the page"


def test_deleted_errand_is_skipped(r, settings):
    _, runner = make(r, settings, FakeFetcher())
    assert runner.run_check("00000000-0000-0000-0000-000000000000") is None


def test_worker_handles_jobs(r, settings, shop):
    url = f"{SHOP}/static"
    fetcher = FakeFetcher({(url, False): static_html(shop)})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("A", price_spec(), None)
    w = Worker(runner, r)
    queue.enqueue_check(r, e["id"])
    reply = queue.push_dryrun(r, price_spec())
    for _ in range(2):
        w.handle(queue.pop_job(r, 1))
    assert store.get_errand(e["id"])["last_status"] == "baseline"
    assert queue.wait_reply(r, reply, 1)["value"] == 134990
    w.handle({"type": "bogus"})  # ignored, no crash


def test_short_page_without_scripts_is_not_rendered(r, settings, shop):
    url = f"{SHOP}/notices"
    fetcher = FakeFetcher({(url, False): shop.get("/notices").text})
    store, runner = make(r, settings, fetcher)
    e = store.create_errand("Board", {"name": "Board", "url": url, "kind": "page_changed", "every_minutes": 5}, None)
    runner.run_check(e["id"])
    assert fetcher.calls == [(url, False)]


def test_expired_dry_run_is_skipped(r, settings, shop):
    fetcher = FakeFetcher({(f"{SHOP}/static", False): static_html(shop)})
    _, runner = make(r, settings, fetcher)
    reply = queue.push_dryrun(r, price_spec(), timeout_s=-1)  # the API already gave up
    Worker(runner, r).handle(queue.pop_job(r, 1))
    assert fetcher.calls == []
    assert r.llen(reply) == 0
