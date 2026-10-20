import json

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from tests.fakes import MemoryStore
from vigil import demo_shop
from vigil.config import Settings
from vigil.notifier import Notifier, ntfy_target


def test_ntfy_target():
    assert ntfy_target("https://ntfy.sh/my-topic") == ("https://ntfy.sh", "my-topic")
    assert ntfy_target("https://example.com/ntfy/t1") == ("https://example.com/ntfy", "t1")
    assert ntfy_target("https://ntfy.sh") is None


def test_inbox_only_by_default(r):
    store = MemoryStore()
    n = Notifier(store, r, Settings())
    row = n.process({"errand_id": None, "title": "Price drop: ₹1,14,990", "body": "b"})
    assert row["delivered_to"] == "inbox"
    assert store.notifications[0]["title"] == "Price drop: ₹1,14,990"


@respx.mock
def test_ntfy_and_telegram(r):
    ntfy = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
    tg = respx.post("https://api.telegram.org/botTOKEN/sendMessage").mock(return_value=httpx.Response(200))
    s = Settings(ntfy_url="https://ntfy.sh/vigil-test", telegram_bot_token="TOKEN", telegram_chat_id="42")
    store = MemoryStore()
    row = Notifier(store, r, s).process({"errand_id": None, "title": "₹ title", "body": "body"})
    assert row["delivered_to"] == "inbox,ntfy,telegram"
    sent = json.loads(ntfy.calls[0].request.content)
    assert sent == {"topic": "vigil-test", "title": "₹ title", "message": "body", "tags": ["eyes"]}
    assert json.loads(tg.calls[0].request.content)["chat_id"] == "42"


@respx.mock
def test_channel_failure_still_stores_inbox(r):
    respx.post("https://ntfy.sh").mock(return_value=httpx.Response(500))
    store = MemoryStore()
    row = Notifier(store, r, Settings(ntfy_url="https://ntfy.sh/t")).process({"title": "x", "body": "y"})
    assert row["delivered_to"] == "inbox"


def test_demo_shop_routes():
    demo_shop.reset_state()
    c = TestClient(demo_shop.app)
    assert "Loading…" in c.get("/").text
    assert "₹" not in c.get("/").text  # price only arrives via JavaScript
    assert c.get("/api/product").json()["price"] == 134990
    assert "<span class='price-now'>₹1,34,990</span>" in c.get("/static").text
    assert c.get("/robots.txt").text.startswith("User-agent: *")
    assert "Demo shop admin" in c.get("/admin").text
    c.post("/admin/price", json={"value": 114990})
    c.post("/admin/stock", json={"in_stock": True})
    c.post("/admin/layout", json={"layout": 2})
    c.post("/admin/notice", json={"text": "Slot available <b>"})
    static = c.get("/static").text
    assert "<div class='pdp-amount'><b>₹1,14,990</b></div>" in static and "In stock" in static
    assert "Slot available &lt;b&gt;" in c.get("/notices").text  # escaped
    assert c.post("/admin/layout", json={"layout": 3}).status_code == 422
    c.post("/admin/reset")
    assert c.get("/api/product").json()["price"] == 134990


class FlakyStore(MemoryStore):
    def __init__(self, failures):
        super().__init__()
        self.failures = failures

    def add_notification(self, *a, **kw):
        if self.failures:
            self.failures -= 1
            raise RuntimeError("db down")
        return super().add_notification(*a, **kw)


@respx.mock
def test_db_retry_does_not_resend_push(r):
    ntfy = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
    store = FlakyStore(failures=1)
    n = Notifier(store, r, Settings(ntfy_url="https://ntfy.sh/t"))
    msg = {"errand_id": None, "title": "x", "body": "y"}
    with pytest.raises(RuntimeError):
        n.process(msg)
    row = n.process(msg)  # retry after the database came back
    assert len(ntfy.calls) == 1
    assert row["delivered_to"] == "inbox,ntfy"


@respx.mock
def test_requeued_message_is_not_pushed_again(r):
    ntfy = respx.post("https://ntfy.sh").mock(return_value=httpx.Response(200))
    store = MemoryStore()
    row = Notifier(store, r, Settings(ntfy_url="https://ntfy.sh/t")).process(
        {"title": "x", "body": "y", "delivered": ["ntfy"]}
    )
    assert ntfy.calls.call_count == 0
    assert row["delivered_to"] == "inbox,ntfy"
