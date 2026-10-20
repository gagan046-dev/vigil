import json

import httpx
import respx

from vigil import queue
from vigil.config import Settings
from vigil.llm import LLMClient, first_json_object


def test_enqueue_dedupes_and_pop_clears_marker(r):
    assert queue.enqueue_check(r, "e1") is True
    assert queue.enqueue_check(r, "e1") is False
    assert queue.queue_depth(r) == 1
    job = queue.pop_job(r, timeout_s=1)
    assert job == {"type": "check", "errand_id": "e1"}
    assert not r.sismember(queue.QUEUED, "e1")
    assert queue.enqueue_check(r, "e1") is True


def test_stale_queued_marker_is_ignored(r):
    r.sadd(queue.QUEUED, "e2")  # marker left behind but list is empty
    assert queue.enqueue_check(r, "e2") is True
    assert queue.queue_depth(r) == 1


def test_dryrun_reply_roundtrip(r):
    key = queue.push_dryrun(r, {"name": "x"})
    job = queue.pop_job(r, timeout_s=1)
    assert job["type"] == "dryrun" and job["reply"] == key
    queue.push_reply(r, key, {"ok": True})
    assert r.ttl(key) > 0
    assert queue.wait_reply(r, key, 1) == {"ok": True}


def test_push_reply_rejects_foreign_keys(r):
    queue.push_reply(r, "vigil:checks", {"ok": True})
    assert queue.queue_depth(r) == 0


def test_malformed_job_ignored(r):
    r.rpush(queue.CHECKS, "not json")
    assert queue.pop_job(r, timeout_s=1) is None


def test_notify_roundtrip(r):
    queue.push_notify(r, "e1", "T", "B")
    assert queue.pop_notify(r, 1) == {"errand_id": "e1", "title": "T", "body": "B"}


def test_budget(r):
    assert queue.llm_budget_take(r, 2)
    assert queue.llm_budget_take(r, 2)
    assert not queue.llm_budget_take(r, 2)
    assert queue.llm_budget_used(r) == 3
    assert 0 < r.ttl(queue.budget_key()) <= 2 * 24 * 3600


def test_host_politeness(r):
    slept = []
    assert queue.wait_host_slot(r, "a.test", 5000, max_wait_s=10, sleep=slept.append)
    assert r.pttl("vigil:host:a.test") > 0
    # second fetch must wait; with a tiny max wait it gives up and proceeds
    assert queue.wait_host_slot(r, "a.test", 5000, max_wait_s=0.01, sleep=lambda s: None) is False
    assert queue.wait_host_slot(r, "b.test", 5000, sleep=slept.append)
    assert slept == []


def test_first_json_object():
    assert first_json_object('Sure! ```json\n{"a": 1, "b": {"c": 2}}\n``` done') == {"a": 1, "b": {"c": 2}}
    assert first_json_object('{bad} then {"x": 3}') == {"x": 3}
    assert first_json_object("no json") is None
    assert first_json_object("") is None


def test_fake_mode_never_calls(r):
    llm = LLMClient(Settings(llm_mode="fake"), r)
    assert not llm.available()
    assert llm.chat_json("s", "u") is None


@respx.mock
def test_gateway_call_budget_and_auth(r):
    s = Settings(llm_mode="gateway", llm_base_url="http://gw.test/v1", llm_api_key="sk-test", llm_daily_budget=1)
    route = respx.post("http://gw.test/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": 'here: {"value": 5}'}}]})
    )
    llm = LLMClient(s, r)
    assert llm.available()
    assert llm.chat_json("sys", "user", purpose="extract") == {"value": 5}
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "vigil-smart" and sent["messages"][0]["content"] == "sys"
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"
    assert not llm.available()
    assert llm.chat_json("sys", "user") is None  # budget exhausted
    assert route.call_count == 1


@respx.mock
def test_gateway_errors_return_none(r):
    s = Settings(llm_mode="gateway", llm_base_url="http://gw.test/v1")
    respx.post("http://gw.test/v1/chat/completions").mock(return_value=httpx.Response(500))
    assert LLMClient(s, r).chat_json("s", "u") is None


def test_dryrun_jumps_the_queue_and_expires(r):
    queue.enqueue_check(r, "e1")
    queue.push_dryrun(r, {"name": "x"}, timeout_s=60)
    job = queue.pop_job(r, timeout_s=1)
    assert job["type"] == "dryrun"  # a person is waiting: served before the backlog
    assert not queue.job_expired(job)
    assert queue.job_expired(job, now=job["expires_at"] + 1)
    assert not queue.job_expired({"type": "dryrun"})  # older jobs without a deadline still run
