import pytest
from pydantic import ValidationError

from vigil.models import ErrandSpec, cron_for, describe_condition, snap_interval


@pytest.mark.parametrize(
    "minutes,expected",
    [
        (1, 1),
        (3, 2),
        (4, 5),
        (7, 5),
        (8, 10),
        (25, 30),
        (45, 60),
        (90, 120),
        (100, 120),
        (240, 180),
        (300, 360),
        (1000, 720),
        (1100, 1440),
        (5000, 1440),
        (0, 1),
    ],
)
def test_snap_interval(minutes, expected):
    assert snap_interval(minutes) == expected


@pytest.mark.parametrize(
    "minutes,cron",
    [
        (1, "*/1 * * * *"),
        (5, "*/5 * * * *"),
        (30, "*/30 * * * *"),
        (60, "0 * * * *"),
        (120, "0 */2 * * *"),
        (180, "0 */3 * * *"),
        (360, "0 */6 * * *"),
        (720, "0 */12 * * *"),
        (1440, "0 8 * * *"),
    ],
)
def test_cron_for(minutes, cron):
    assert cron_for(minutes) == cron


def _spec(**kw):
    base = {"name": "n", "url": "https://example.com/p", "kind": "page_changed"}
    base.update(kw)
    return ErrandSpec(**base)


def test_price_requires_positive_threshold():
    with pytest.raises(ValidationError):
        _spec(kind="price_below")
    with pytest.raises(ValidationError):
        _spec(kind="price_below", threshold=0)
    assert _spec(kind="price_above", threshold=10).threshold == 10


def test_text_requires_keyword():
    with pytest.raises(ValidationError):
        _spec(kind="text_appears")
    with pytest.raises(ValidationError):
        _spec(kind="text_appears", keyword="x" * 121)
    assert _spec(kind="text_disappears", keyword="Sold out").keyword == "Sold out"


def test_url_scheme():
    with pytest.raises(ValidationError):
        _spec(url="ftp://example.com")
    with pytest.raises(ValidationError):
        _spec(url="file:///etc/passwd")
    with pytest.raises(ValidationError):
        _spec(url="https://")


def test_every_minutes_snapped_and_min_interval(monkeypatch):
    assert _spec(every_minutes=40).every_minutes == 30
    monkeypatch.setenv("MIN_INTERVAL_MINUTES", "5")
    with pytest.raises(ValidationError):
        _spec(every_minutes=1)
    assert _spec(every_minutes=5).every_minutes == 5


def test_name_length():
    with pytest.raises(ValidationError):
        _spec(name="")
    with pytest.raises(ValidationError):
        _spec(name="x" * 81)


def test_blank_selector_becomes_none():
    assert _spec(selector="  ").selector is None


def test_describe_condition():
    assert describe_condition({"kind": "price_below", "threshold": 120000}) == "price < 1,20,000"
    assert describe_condition({"kind": "text_appears", "keyword": "Slot"}) == '"Slot" appears'
    assert describe_condition({"kind": "page_changed"}) == "page changes"


def test_best_price_spec_rules():
    from vigil.models import ErrandSpec, describe_condition

    s = ErrandSpec(name="Best", kind="best_price_below", query=" Aurora 15 ")
    assert s.url is None and s.query == "Aurora 15" and s.threshold is None and s.host == ""
    assert ErrandSpec(name="B", kind="best_price_below", url="https://x.example.com/p").query is None
    for bad in (
        {"name": "B", "kind": "best_price_below"},
        {"name": "B", "kind": "best_price_below", "query": "x", "threshold": -5},
        {"name": "B", "kind": "best_price_below", "url": "ftp://x/y"},
        {"name": "B", "kind": "page_changed"},  # other kinds still need a url
    ):
        with pytest.raises(ValueError):
            ErrandSpec(**bad)
    assert describe_condition({"kind": "best_price_below", "threshold": 125000}) == "best price < 1,25,000"
    assert describe_condition({"kind": "best_price_below"}) == "best price drops"
