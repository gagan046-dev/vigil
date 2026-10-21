"""Pagination envelopes, detail endpoints, the inbox and the price-comparison API."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeScheduler, MemoryStore
from vigil import queue
from vigil.api import create_app
from vigil.config import Settings
from vigil.llm import LLMClient

SPEC = {"name": "Aurora", "url": "https://shop.example.com/a", "kind": "price_below", "threshold": 120000}


@pytest.fixture
def env(r, settings):
    store, sched = MemoryStore(), FakeScheduler()
    app = create_app(settings=settings, store=store, r=r, scheduler=sched, llm=LLMClient(settings, r), background=False)
    with TestClient(app) as client:
        yield client, store, r


def make_errands(store, n):
    ids = []
    for i in range(n):
        e = store.create_errand(f"Watch {i:02d}", dict(SPEC, url=f"https://shop.example.com/{i}"), None)
        store.errands[e["id"]]["created_at"] = datetime.now(UTC) - timedelta(minutes=i)
        ids.append(e["id"])
    return ids


def test_errand_pagination_envelope_and_bounds(env):
    client, store, _ = env
    make_errands(store, 30)
    first = client.get("/api/errands?per_page=25").json()
    assert {k: first[k] for k in ("total", "page", "per_page", "pages")} == {
        "total": 30,
        "page": 1,
        "per_page": 25,
        "pages": 2,
    }
    assert len(first["items"]) == 25 and first["items"][0]["name"] == "Watch 00"
    second = client.get("/api/errands?page=2&per_page=25").json()
    assert len(second["items"]) == 5 and second["items"][-1]["name"] == "Watch 29"
    beyond = client.get("/api/errands?page=9&per_page=25").json()
    assert beyond["items"] == [] and beyond["total"] == 30
    assert client.get("/api/errands?per_page=101").status_code == 422
    assert client.get("/api/errands?page=0").status_code == 422
    assert client.get("/api/errands?status=weird").status_code == 422
    empty = MemoryStore().page_errands()
    assert empty == ([], 0)


def test_errand_search_and_status_filters(env):
    client, store, _ = env
    ids = make_errands(store, 6)
    store.set_active(ids[0], False)
    store.update_errand_state(ids[1], consecutive_failures=4)
    assert client.get("/api/errands?status=paused").json()["total"] == 1
    failing = client.get("/api/errands?status=failing").json()
    assert failing["total"] == 1 and failing["items"][0]["status"] == "failing"
    assert client.get("/api/errands?status=active").json()["total"] == 4
    assert client.get("/api/errands?q=watch 03").json()["items"][0]["id"] == ids[3]
    assert client.get("/api/errands?q=shop.example.com/5").json()["total"] == 1
    assert client.get("/api/errands?q=nothing-like-this").json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "per_page": 25,
        "pages": 1,
    }


def test_errand_detail_history_and_paged_checks(env):
    client, store, _ = env
    eid = make_errands(store, 1)[0]
    for i, v in enumerate([130000.0, 125000.0, 125000.0, 119000.0]):
        store.add_check(eid, "triggered" if v < 120000 else "changed", 10, v, False, False, f"#{i}")
    store.update_errand_state(eid, last_value=119000.0)
    store.checks[0]["at"] = datetime.now(UTC) - timedelta(days=40)

    d = client.get(f"/api/errands/{eid}").json()
    assert d["spec"]["threshold"] == 120000 and d["schedule"] == "0 * * * *" and d["condition"] == "price < 1,20,000"
    assert d["status"] == "active" and d["latest_compare"] is None and "last_text" not in d
    assert "next_run_at" in d  # None with the fake scheduler
    listed = client.get("/api/errands").json()["items"][0]
    assert listed["prev_value"] == 125000.0 and listed["change"] == -6000.0

    hist = client.get(f"/api/errands/{eid}/history?days=30").json()
    assert [h["value"] for h in hist] == [125000.0, 125000.0, 119000.0] and set(hist[0]) == {"at", "value", "status"}
    assert len(client.get(f"/api/errands/{eid}/history?days=60").json()) == 4
    assert client.get(f"/api/errands/{eid}/history?days=0").status_code == 422

    page = client.get(f"/api/errands/{eid}/checks?page=2&per_page=3").json()
    assert page["total"] == 4 and page["pages"] == 2 and [c["message"] for c in page["items"]] == ["#0"]
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/api/errands/{missing}").status_code == 404
    assert client.get(f"/api/errands/{missing}/history").status_code == 404


def test_log_filters(env):
    client, store, _ = env
    a, b = make_errands(store, 2)
    store.add_check(a, "error", message="boom")
    store.add_check(a, "unchanged")
    store.add_check(b, "error", message="bang")
    assert client.get("/api/log").json()["total"] == 3
    errors = client.get("/api/log?status=error").json()
    assert errors["total"] == 2 and {i["errand_name"] for i in errors["items"]} == {"Watch 00", "Watch 01"}
    only_a = client.get(f"/api/log?errand_id={a}&status=error").json()
    assert only_a["total"] == 1 and only_a["items"][0]["message"] == "boom"
    assert client.get("/api/log?status=bogus").status_code == 422
    assert client.get("/api/log?errand_id=not-a-uuid").status_code == 422


def test_inbox_unread_filter_count_and_read_all(env):
    client, store, _ = env
    eid = make_errands(store, 1)[0]
    rows = [store.add_notification(eid, f"T{i}", "B", "inbox") for i in range(5)]
    client.post(f"/api/inbox/{rows[0]['id']}/read")
    assert client.get("/api/inbox/unread-count").json() == {"count": 4}
    unread = client.get("/api/inbox?unread=true&per_page=2").json()
    assert unread["total"] == 4 and unread["pages"] == 2 and unread["items"][0]["title"] == "T4"
    assert client.get("/api/inbox?unread=false").json()["total"] == 1
    assert client.post("/api/inbox/read-all").json() == {"ok": True, "updated": 4}
    assert client.get("/api/inbox/unread-count").json() == {"count": 0}
    assert client.post("/api/inbox/read-all").json()["updated"] == 0


def test_compare_api_lifecycle(env):
    client, store, r = env
    res = client.post("/api/compare", json={"query": "  Aurora 15   RTX 5070 ", "max_pages": 5})
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued"
    job = queue.pop_job(r, 1)
    assert job == {"type": "compare", "compare_id": body["id"], "max_pages": 5}
    row = client.get(f"/api/compare/{body['id']}").json()
    assert row["query"] == "Aurora 15 RTX 5070" and row["offers"] == []

    # not finished yet -> cannot be tracked
    assert client.post(f"/api/compare/{body['id']}/track", json={}).status_code == 409
    best = {"seller": "bazaar99", "seller_name": "Bazaar99", "price": 124499.0, "currency": "₹"}
    store.update_compare(
        body["id"],
        status="done",
        product={"query": "Aurora 15 RTX 5070", "title": "Aurora 15 (RTX 5070) Gaming Laptop - Bazaar99"},
        offers=[best],
        best=best,
    )
    listed = client.get("/api/compare?per_page=10").json()
    assert listed["total"] == 1 and listed["items"][0]["offers_count"] == 1 and "offers" not in listed["items"][0]

    tracked = client.post(f"/api/compare/{body['id']}/track", json={"threshold": 120000, "every_minutes": 60})
    assert tracked.status_code == 201
    e = tracked.json()
    assert e["spec"]["kind"] == "best_price_below" and e["spec"]["query"] == "Aurora 15 (RTX 5070) Gaming Laptop"
    assert e["spec"]["url"] is None and e["condition"] == "best price < 1,20,000"
    assert e["name"] == "Best price: Aurora 15 (RTX 5070) Gaming Laptop"
    assert queue.pop_job(r, 1)["errand_id"] == e["id"]  # immediate first check
    assert client.post(f"/api/compare/{body['id']}/track", json={"threshold": -1}).status_code == 422


def test_compare_url_query_and_validation(env):
    client, store, r = env
    url = "https://www.flipkart.com/apple-iphone-16/p/itm1"
    body = client.post("/api/compare", json={"query": url}).json()
    store.update_compare(body["id"], status="done", product={"query": url, "title": None, "url": url})
    e = client.post(f"/api/compare/{body['id']}/track", json={}).json()
    assert e["spec"]["url"] == url and e["spec"]["query"] is None and e["condition"] == "best price drops"
    for bad in ("a", "ftp://x.example.com/p", "https://", "x" * 501):
        assert client.post("/api/compare", json={"query": bad}).status_code == 422, bad
    assert client.post("/api/compare", json={"query": "ok query", "max_pages": 50}).status_code == 422
    assert client.get("/api/compare/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/compare/nope").status_code == 422


def test_errand_detail_links_latest_comparison(env):
    client, store, _ = env
    e = client.post(
        "/api/errands", json={"spec": {"name": "Best", "kind": "best_price_below", "query": "Aurora 15"}}
    ).json()
    assert e["spec"]["url"] is None and e["schedule"] == "0 * * * *"
    run = store.create_compare("Aurora 15", errand_id=e["id"])
    d = client.get(f"/api/errands/{e['id']}").json()
    assert d["latest_compare"]["id"] == run["id"]
    assert client.get(f"/api/compare?errand_id={e['id']}").json()["total"] == 1
    assert client.get("/api/compare").json()["total"] == 0


def test_system_reports_search_settings(r):
    s = Settings(env="test", search_provider="searxng", compare_sellers=("flipkart.com", "shop.example"))
    app = create_app(settings=s, store=MemoryStore(), r=r, scheduler=FakeScheduler(), background=False)
    with TestClient(app) as client:
        body = client.get("/api/system").json()
    assert body["search_provider"] == "searxng" and body["compare_max_pages"] == 8
    assert body["compare_sellers"] == [
        {"key": "flipkart.com", "name": "Flipkart", "domain": "flipkart.com"},
        {"key": "shop.example", "name": "Shop", "domain": "shop.example"},
    ]
