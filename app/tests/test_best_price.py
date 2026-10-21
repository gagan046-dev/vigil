"""Compare jobs in the worker, and best_price_below watches (fire on transition)."""

import json

import pytest
from fastapi.testclient import TestClient

from tests.fakes import MemoryStore
from tests.test_compare import DEMO, SHOP, DemoFetcher
from vigil import demo_shop, queue
from vigil.models import ErrandSpec
from vigil.search import DemoSearch
from vigil.worker import CheckRunner, Worker


@pytest.fixture
def shop():
    demo_shop.reset_state()
    client = TestClient(demo_shop.app, base_url=SHOP)
    yield client
    demo_shop.reset_state()


@pytest.fixture
def env(r, shop):
    store = MemoryStore()
    runner = CheckRunner(store, r, DemoFetcher(shop), None, DEMO, search=DemoSearch(DEMO))
    return store, runner, shop, r


def notes(r):
    return [json.loads(x) for x in r.lrange(queue.NOTIFY, 0, -1)]


def spec(**kw):
    data = {"name": "Best price: Aurora 15", "kind": "best_price_below", "query": "Aurora 15", "every_minutes": 60}
    return ErrandSpec(**{**data, **kw}).model_dump()


def test_compare_job_via_worker(env):
    store, runner, _, r = env
    run = store.create_compare("Aurora 15 RTX 5070")
    queue.enqueue_compare(r, run["id"], 6)
    job = queue.pop_job(r, 1)
    assert job == {"type": "compare", "compare_id": run["id"], "max_pages": 6}
    Worker(runner, r).handle(job)
    done = store.get_compare(run["id"])
    assert done["status"] == "done" and done["finished_at"] is not None
    assert done["best"]["seller"] == "bazaar99" and len(done["offers"]) == 5
    assert done["notes"]["provider"] == "demo"
    # a job for a deleted comparison is skipped quietly
    assert runner.run_compare("00000000-0000-0000-0000-000000000000") is None


def test_compare_crash_marks_the_run_as_error(env, monkeypatch):
    store, runner, _, _ = env
    run = store.create_compare("aurora")
    monkeypatch.setattr(runner.search, "search", lambda *a: 1 / 0)  # search errors are tolerated...
    assert runner.run_compare(run["id"]) == "done"
    assert any("Search failed" in m for m in store.get_compare(run["id"])["notes"]["messages"])

    def boom(*a, **k):
        raise RuntimeError("bug")

    monkeypatch.setattr("vigil.compare.Comparer.run", boom)  # ...a pipeline bug is recorded
    assert runner.run_compare(run["id"]) == "error"
    assert store.get_compare(run["id"])["status"] == "error"


def test_best_price_below_threshold_fires_on_transition(env):
    store, runner, shop, r = env
    e = store.create_errand("Best price: Aurora 15", spec(threshold=125000), None)

    assert runner.run_check(e["id"]) == "triggered"  # Bazaar99 at 1,24,499 < 1,25,000
    [n] = notes(r)
    assert n["title"] == "Best price: Aurora 15 at Bazaar99 ₹1,24,499 (target < ₹1,25,000)"
    assert n["body"].endswith("/sellers/bazaar99/aurora-15")
    errand = store.get_errand(e["id"])
    assert errand["last_value"] == 124499.0 and errand["condition_met"] is True
    assert store.latest_compare(e["id"])["best"]["seller"] == "bazaar99"

    assert runner.run_check(e["id"]) == "unchanged"  # still below: no second alert
    assert len(notes(r)) == 1

    shop.post("/admin/sellers/bazaar99", json={"price": 135000})  # best is now PixelMart 1,29,990: re-arm
    assert runner.run_check(e["id"]) == "changed"
    assert store.get_errand(e["id"])["condition_met"] is False

    shop.post("/admin/sellers/northwind", json={"price": 121000})
    assert runner.run_check(e["id"]) == "triggered"
    assert "Northwind Electronics ₹1,21,000" in notes(r)[-1]["title"]
    checks = store.list_checks(e["id"])
    assert checks[0]["value"] == 121000.0 and "best ₹1,21,000 at Northwind Electronics" in checks[0]["message"]
    page, total = store.page_compares(0, 10, e["id"])
    assert total == 4 and all(p["errand_id"] == e["id"] for p in page)
    assert store.page_compares(0, 10)[1] == 0  # tracked runs are not in the manual history


def test_best_price_without_threshold_alerts_on_every_drop(env):
    store, runner, shop, r = env
    e = store.create_errand("Best price: Aurora 15", spec(), None)
    assert runner.run_check(e["id"]) == "baseline" and notes(r) == []
    shop.post("/admin/sellers/pixelmart", json={"price": 119000})
    assert runner.run_check(e["id"]) == "triggered"
    assert notes(r)[-1]["title"] == "Best price: Aurora 15 at PixelMart ₹1,19,000 (was ₹1,24,499)"
    shop.post("/admin/sellers/pixelmart", json={"price": 125000})  # rises: no alert
    assert runner.run_check(e["id"]) == "changed" and len(notes(r)) == 1


def test_best_price_failure_counts_and_prunes(env):
    store, runner, _, r = env
    e = store.create_errand("Best price: Kettle", spec(query="Kettle 1.5L", name="Best price: Kettle"), None)
    for _ in range(3):
        assert runner.run_check(e["id"]) == "error"
    errand = store.get_errand(e["id"])
    assert errand["consecutive_failures"] == 3
    assert notes(r)[-1]["title"].startswith("Errand failing: no seller returned a matching price")
    assert notes(r)[-1]["body"].endswith("Kettle 1.5L")
    for _ in range(3):
        runner.run_check(e["id"])
    store.prune_compares(e["id"], keep=2)
    assert store.page_compares(0, 10, e["id"])[1] == 2


def test_best_price_dry_run(env):
    _, runner, _, _ = env
    res = runner.dry_run(spec(threshold=125000, every_minutes=15))
    assert res["ok"] is True and res["value"] == 124499.0 and res["condition_true"] is True
    assert "Bazaar99" in res["message"] and "60 minutes" in res["message"]
    assert "PixelMart" in res["excerpt"]
    miss = runner.dry_run(spec(query="Kettle"))
    assert miss["ok"] is False and "No seller" in miss["message"]
