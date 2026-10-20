import pytest
from fastapi.testclient import TestClient

from vigil import demo_shop
from vigil.extract import (
    css_selector_for,
    find_price,
    format_money,
    group_indian,
    hash_text,
    heuristic_price,
    page_label,
    page_lines,
    parse_html,
    parse_price,
    select_price,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("₹1,20,000", 120000),
        ("₹ 1,34,990", 134990),
        ("Rs. 1,34,990/-", 134990),
        ("Rs 999", 999),
        ("INR 45,500", 45500),
        ("$1,299.99", 1299.99),
        ("€49.90", 49.90),
        ("£1,000", 1000),
        ("1.2 lakh", 120000),
        ("2 lakhs", 200000),
        ("1.5 crore", 15000000),
        ("120k", 120000),
        ("120K", 120000),
        ("12,345.67", 12345.67),
        ("Price: 89999 only", 89999),
        ("just 1,20,000.50 rupees", 120000.50),
    ],
)
def test_parse_price_formats(text, expected):
    assert parse_price(text) == pytest.approx(expected)


def test_parse_price_none():
    assert parse_price("no numbers here") is None
    assert parse_price("") is None


def test_currency_detection_and_require_currency():
    assert find_price("₹1,34,990").currency == "₹"
    assert find_price("Rs. 500").currency == "₹"
    assert find_price("USD 20").currency == "$"
    pm = find_price("RTX 5070 now ₹1,14,990", require_currency=True)
    assert pm.value == 114990
    assert find_price("RTX 5070", require_currency=True) is None


def test_rs_not_matched_inside_words():
    pm = find_price("2 hours 5 left", require_currency=True)
    assert pm is None


def test_indian_grouping_and_money():
    assert group_indian(120000) == "1,20,000"
    assert group_indian(134990) == "1,34,990"
    assert group_indian(999) == "999"
    assert group_indian(10000000) == "1,00,00,000"
    assert format_money(114990, "₹") == "₹1,14,990"
    assert format_money(1299.5, "$") == "$1,299.50"
    assert format_money(None) == "-"


def test_visible_text_drops_scripts_and_collapses():
    soup = parse_html(
        "<html><head><title>T</title><style>.x{}</style></head><body>"
        "<h1>  Hello   world </h1><script>var a=1</script><noscript>no</noscript>"
        "<p>Line\n two</p></body></html>"
    )
    lines, matched = page_lines(soup)
    assert lines == ["Hello world", "Line two"]
    assert matched is False
    assert page_label(soup) == "Hello world"


def test_selector_text_and_invalid_selector():
    soup = parse_html("<body><div id='a'>A text</div><div>B</div></body>")
    assert page_lines(soup, "#a") == (["A text"], True)
    assert page_lines(soup, "###bad[[") == (["A text", "B"], False)


def test_hash_is_sha256():
    assert hash_text("x") == hash_text("x")
    assert len(hash_text("x")) == 64


def test_heuristic_prefers_specific_and_skips_old_price():
    html = (
        "<body><div class='price-box'><span class='old-price'>₹1,49,990</span>"
        "<span class='price-now'>₹1,34,990</span></div></body>"
    )
    soup = parse_html(html)
    pm, el = heuristic_price(soup)
    assert pm.value == 134990
    assert el["class"] == ["price-now"]


def test_heuristic_itemprop_meta():
    soup = parse_html("<body><meta itemprop='price' content='4999'><p>stuff</p></body>")
    pm, el = heuristic_price(soup)
    assert pm.value == 4999


def test_css_selector_unique_and_verified():
    html = (
        "<body><ul><li class='item'><span class='v'>1</span></li>"
        "<li class='item special'><span class='v'>2</span></li></ul></body>"
    )
    soup = parse_html(html)
    target = soup.select("span.v")[1]
    sel = css_selector_for(target, soup)
    assert sel is not None
    assert soup.select(sel) == [target]


def test_css_selector_prefers_id():
    soup = parse_html("<body><span id='price' class='a b'>₹5</span></body>")
    assert css_selector_for(soup.find("span"), soup) == "span#price"


def _static_html(layout):
    demo_shop.reset_state()
    client = TestClient(demo_shop.app)
    client.post("/admin/layout", json={"layout": layout})
    html = client.get("/static").text
    demo_shop.reset_state()
    return html


def test_demo_shop_layout_1_and_2_selectors():
    soup1 = parse_html(_static_html(1))
    pm1, el1 = heuristic_price(soup1)
    sel1 = css_selector_for(el1, soup1)
    assert pm1.value == 134990
    assert sel1 == "span.price-now"

    soup2 = parse_html(_static_html(2))
    # the learned selector from layout 1 breaks on layout 2 ...
    assert select_price(soup2, sel1) is None
    # ... and the heuristic finds the new element, giving a healed selector
    pm2, el2 = heuristic_price(soup2)
    sel2 = css_selector_for(el2, soup2)
    assert pm2.value == 134990
    assert sel2 == "div.pdp-amount"
    assert select_price(soup2, sel2)[0].value == 134990


def test_format_number_rounds_before_splitting():
    from vigil.extract import format_number

    assert format_number(9.999) == "10"
    assert format_number(120000.5) == "1,20,000.50"
