import threading

import pytest
from fastapi.testclient import TestClient

from tests.fakes import FakeScheduler, MemoryStore
from vigil import queue
from vigil.api import create_app
from vigil.llm import LLMClient

SPEC = {
    "name": "Aurora",
    "url": "https://shop.example.com/a",
    "kind": "price_below",
    "threshold": 120000,
    "every_minutes": 30,
}


@pytest.fixture
def env(r, settings):
    store, sched = MemoryStore(), FakeScheduler()
    app = create_app(settings=settings, store=store, r=r, scheduler=sched, llm=LLMClient(settings, r), background=False)
    with TestClient(app) as client:
        yield client, store, sched, r


def test_health_ready_metrics(env):
    client, *_ = env
    assert client.get("/healthz").json() == {"ok": True}
    ready = client.get("/readyz")
    assert ready.status_code == 200 and ready.json()["schema"] is True
    assert "vigil_api_plans_total" in client.get("/metrics").text


def test_readyz_fails_when_db_down(env):
    client, store, *_ = env
    store.ping = lambda: (_ for _ in ()).throw(RuntimeError("down"))
    res = client.get("/readyz")
    assert res.status_code == 503 and res.json()["db"] is False


def test_plan(env):
    client, *_ = env
    res = client.post("/api/plan", json={"text": "tell me when https://shop.example.com/a drops below ₹1,20,000"})
    body = res.json()
    assert res.status_code == 200
    assert body["source"] == "rules" and body["spec"]["kind"] == "price_below"
    assert body["spec"]["threshold"] == 120000
    assert isinstance(body["notes"], list)


def test_plan_without_url_is_422(env):
    client, *_ = env
    res = client.post("/api/plan", json={"text": "tell me when it is cheap"})
    assert res.status_code == 422 and "web address" in res.json()["detail"]


def test_create_list_patch_delete(env):
    client, store, sched, r = env
    res = client.post("/api/errands", json={"spec": SPEC, "prompt": "watch it"})
    assert res.status_code == 201
    e = res.json()
    assert e["schedule"] == "*/30 * * * *" and e["active"] is True and e["condition"] == "price < 1,20,000"
    assert "last_text" not in e
    assert sched.upserts[-1]["id"] == e["id"]
    assert queue.queue_depth(r) == 1  # immediate check

    listed = client.get("/api/errands").json()
    assert [x["id"] for x in listed["items"]] == [e["id"]] and listed["total"] == 1
    assert listed["items"][0]["status"] == "active"

    paused = client.patch(f"/api/errands/{e['id']}", json={"active": False}).json()
    assert paused["active"] is False and sched.upserts[-1]["active"] is False

    assert client.post(f"/api/errands/{e['id']}/check").status_code == 202
    assert client.delete(f"/api/errands/{e['id']}").status_code == 204
    assert sched.deleted == [e["id"]]
    assert client.get("/api/errands").json()["items"] == []


def test_errand_validation_and_404(env):
    client, *_ = env
    bad = dict(SPEC, threshold=None)
    assert client.post("/api/errands", json={"spec": bad}).status_code == 422
    assert client.post("/api/errands", json={"spec": dict(SPEC, url="file:///etc/passwd")}).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.patch(f"/api/errands/{missing}", json={"active": True}).status_code == 404
    assert client.delete(f"/api/errands/{missing}").status_code == 404
    assert client.post("/api/errands/not-a-uuid/check").status_code == 422


def test_checks_log_inbox(env):
    client, store, *_ = env
    e = client.post("/api/errands", json={"spec": SPEC}).json()
    store.add_check(e["id"], "baseline", 10, 134990.0, False, False, "ok")
    assert client.get(f"/api/errands/{e['id']}/checks").json()["items"][0]["status"] == "baseline"
    log = client.get("/api/log?per_page=5").json()
    assert log["items"][0]["errand_name"] == "Aurora"
    n = store.add_notification(e["id"], "T", "B", "inbox")
    inbox = client.get("/api/inbox").json()
    assert inbox["items"][0]["read"] is False and inbox["items"][0]["errand_name"] == "Aurora"
    assert client.post(f"/api/inbox/{n['id']}/read").json() == {"ok": True}
    assert client.get("/api/inbox").json()["items"][0]["read"] is True
    assert client.post("/api/inbox/999/read").status_code == 404


def test_system(env):
    client, *_ = env
    s = client.get("/api/system").json()
    assert s["env"] == "test" and s["llm_mode"] == "fake" and s["queue_depth"] == 0
    assert s["llm_daily_budget"] == 40 and s["min_interval_minutes"] == 1 and s["scheduler"] == "fake"


def test_dryrun_roundtrip_and_timeout(env):
    client, _, _, r = env

    def fake_worker():
        job = queue.pop_job(r, timeout_s=5)
        queue.push_reply(
            r,
            job["reply"],
            {
                "ok": True,
                "value": 1.0,
                "condition_true": True,
                "rendered": False,
                "selector": None,
                "excerpt": "",
                "message": "hi",
            },
        )

    t = threading.Thread(target=fake_worker)
    t.start()
    res = client.post("/api/dryrun", json={"spec": SPEC})
    t.join()
    assert res.status_code == 200 and res.json()["value"] == 1.0
    # nobody answers -> 504 (settings.dryrun_timeout_s = 1)
    assert client.post("/api/dryrun", json={"spec": SPEC}).status_code == 504


def test_internal_trigger_auth(env):
    client, _, _, r = env
    e = client.post("/api/errands", json={"spec": SPEC}).json()
    queue.pop_job(r, 1)
    url = f"/internal/trigger/{e['id']}"
    assert client.post(url).status_code == 401
    assert client.post(url, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.post(url, headers={"Authorization": "test-token-123"}).status_code == 401
    ok = client.post(url, headers={"Authorization": "Bearer test-token-123"})
    assert ok.status_code == 202 and ok.json() == {"queued": True}
    assert queue.queue_depth(r) == 1
    client.patch(f"/api/errands/{e['id']}", json={"active": False})
    assert client.post(url, headers={"Authorization": "Bearer test-token-123"}).json()["queued"] is False


def test_internal_trigger_fails_closed_without_token(r):
    from vigil.config import Settings

    app = create_app(
        settings=Settings(internal_token=""), store=MemoryStore(), r=r, scheduler=FakeScheduler(), background=False
    )
    with TestClient(app) as client:
        res = client.post(
            "/internal/trigger/00000000-0000-0000-0000-000000000000", headers={"Authorization": "Bearer "}
        )
        assert res.status_code == 401
