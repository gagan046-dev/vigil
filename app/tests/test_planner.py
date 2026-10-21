import pytest

from tests.fakes import FakeLLM
from vigil.planner import PlanError, plan

URL = "https://shop.example.com/laptops/aurora-15"


def test_price_below_indian_format_and_frequency():
    res = plan(f"Tell me when the laptop at {URL} drops below ₹1,20,000, check every 30 minutes")
    s = res.spec
    assert res.source == "rules"
    assert s.kind == "price_below"
    assert s.threshold == 120000
    assert s.every_minutes == 30
    assert s.url == URL
    assert s.name == "shop.example.com: price below ₹1,20,000"


@pytest.mark.parametrize(
    "phrase,threshold",
    [
        ("under 1.2 lakh", 120000),
        ("less than Rs. 95,000", 95000),
        ("cheaper than 120k", 120000),
        ("drops to $899.99", 899.99),
        ("below INR 45500", 45500),
        ("under ₹ 2 lakhs", 200000),
    ],
)
def test_price_below_variants(phrase, threshold):
    s = plan(f"ping me when {URL} is {phrase}").spec
    assert s.kind == "price_below"
    assert s.threshold == pytest.approx(threshold)


@pytest.mark.parametrize(
    "phrase", ["above ₹50,000", "over 50k", "more than 50000", "exceeds Rs 50,000", "rises above 50,000"]
)
def test_price_above_variants(phrase):
    s = plan(f"alert when {URL} {phrase}").spec
    assert s.kind == "price_above"
    assert s.threshold == 50000


def test_numbers_in_url_are_ignored():
    s = plan("tell me when https://example.com/item/99999?ref=500 goes under 300").spec
    assert s.threshold == 300
    assert s.url == "https://example.com/item/99999?ref=500"


def test_url_trailing_punctuation_stripped():
    assert plan("watch https://example.com/notices.").spec.url == "https://example.com/notices"


def test_quoted_text_appears():
    s = plan(f"Alert me when 'Slot available' appears on {URL}").spec
    assert s.kind == "text_appears"
    assert s.keyword == "Slot available"


def test_double_quoted_and_curly_quotes():
    assert plan(f'tell me when "Registrations open" shows on {URL}').spec.keyword == "Registrations open"
    assert plan(f"tell me when “Book now” is on {URL}").spec.keyword == "Book now"


def test_text_disappears():
    s = plan(f"let me know when 'Sold out' disappears from {URL}").spec
    assert s.kind == "text_disappears"
    assert s.keyword == "Sold out"
    s2 = plan(f'tell me when {URL} no longer says "Out of stock"').spec
    assert s2.kind == "text_disappears"
    assert s2.keyword == "Out of stock"


def test_in_stock_phrase():
    s = plan(f"tell me when {URL} is back in stock").spec
    assert s.kind == "text_appears"
    assert s.keyword == "in stock"


def test_slot_phrase():
    s = plan(f"notify me when a slot opens at {URL}").spec
    assert s.kind == "text_appears"
    assert s.keyword == "slot"


def test_page_changed_explicit_and_default():
    res = plan("Tell me when https://github.com/org/repo/releases changes")
    assert res.spec.kind == "page_changed"
    assert not any("No clear condition" in n for n in res.notes)
    res2 = plan("watch https://example.com/board")
    assert res2.spec.kind == "page_changed"
    assert any("No clear condition" in n for n in res2.notes)


@pytest.mark.parametrize(
    "phrase,minutes",
    [
        ("every 5 minutes", 5),
        ("every 1 minute", 1),
        ("every minute", 1),
        ("every 15 min", 15),
        ("every 2 hours", 120),
        ("every 3h", 180),
        ("hourly", 60),
        ("every hour", 60),
        ("every half an hour", 30),
        ("half an hour", 30),
        ("daily", 1440),
        ("every day", 1440),
        ("every morning", 1440),
        ("every 6 hrs", 360),
        ("every 40 minutes", 30),
        ("twice a day", 720),
    ],
)
def test_frequencies(phrase, minutes):
    assert plan(f"tell me when {URL} changes, {phrase}").spec.every_minutes == minutes


def test_default_frequency_note():
    res = plan(f"tell me when {URL} changes")
    assert res.spec.every_minutes == 60
    assert any("60 minutes" in n for n in res.notes)


def test_frequency_number_not_used_as_price():
    s = plan(f"tell me when {URL} is below 500 every 10 minutes").spec
    assert s.threshold == 500
    assert s.every_minutes == 10


def test_min_interval_clamped(monkeypatch):
    monkeypatch.setenv("MIN_INTERVAL_MINUTES", "5")
    res = plan(f"tell me when {URL} changes every minute")
    assert res.spec.every_minutes == 5
    assert any("minimum" in n for n in res.notes)


def test_render_detection():
    assert plan(f"tell me when {URL} changes (it is a javascript page)").spec.render is True
    assert plan(f"tell me when {URL} changes").spec.render is False


def test_no_url():
    with pytest.raises(PlanError):
        plan("tell me when the laptop is cheap")
    with pytest.raises(PlanError):
        plan("   ")


def test_llm_not_used_when_rules_confident():
    llm = FakeLLM(replies=[{"kind": "page_changed"}])
    res = plan(f"tell me when {URL} drops below 100", llm)
    assert res.source == "rules"
    assert llm.calls == []


def test_llm_used_for_ambiguous_text_and_url_guarded():
    llm = FakeLLM(
        replies=[
            {
                "name": "Exam results",
                "url": "https://evil.example/x",
                "kind": "text_appears",
                "keyword": "Result declared",
                "every_minutes": 40,
                "notes": ["guessed"],
            }
        ]
    )
    res = plan("keep an eye on https://univ.example.edu/results for me", llm)
    assert res.source == "llm"
    assert res.spec.url == "https://univ.example.edu/results"  # invented URL rejected
    assert res.spec.keyword == "Result declared"
    assert res.spec.every_minutes == 30
    assert "guessed" in res.notes


def test_llm_invalid_reply_falls_back_to_rules():
    llm = FakeLLM(replies=[{"kind": "price_below"}])  # missing threshold -> invalid
    res = plan("keep an eye on https://univ.example.edu/results", llm)
    assert res.source == "rules"
    assert res.spec.kind == "page_changed"
    assert any("rule-based" in n for n in res.notes)


def test_best_price_requests_need_no_url():
    r = plan("Tell me when the best price of Aurora 15 drops below ₹1,25,000")
    assert r.spec.kind == "best_price_below" and r.spec.query == "Aurora 15" and r.spec.threshold == 125000
    assert r.spec.url is None and r.spec.every_minutes == 60 and r.spec.name == "Best price: Aurora 15"
    r = plan("find me the lowest price of Apple iPhone 16 128GB in the market every 15 minutes")
    assert r.spec.query == "Apple iPhone 16 128GB" and r.spec.threshold is None and r.spec.every_minutes == 15
    assert any("60 minutes" in n for n in r.notes)
    r = plan("compare prices for https://www.flipkart.com/apple-iphone-16/p/itm1 daily")
    assert r.spec.url == "https://www.flipkart.com/apple-iphone-16/p/itm1" and r.spec.every_minutes == 1440
    with pytest.raises(PlanError):
        plan("tell me the best price")
