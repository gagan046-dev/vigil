import json

from vigil.offers import availability_of, extract_offer, looks_like_bot_wall, product_title, to_number


def ld(data, raw=False):
    body = data if raw else json.dumps(data)
    return f"<script type='application/ld+json'>{body}</script>"


def page(head="", body=""):
    return f"<html><head><title>Fallback title</title>{head}</head><body>{body}</body></html>"


def test_jsonld_product_offer_with_list_price_and_stock():
    html = page(
        ld(
            {
                "@context": "https://schema.org",
                "@type": "Product",
                "name": "Aurora 15 (RTX 5070)",
                "image": ["https://img.example.com/a.jpg"],
                "offers": {
                    "@type": "Offer",
                    "price": "129990.00",
                    "priceCurrency": "INR",
                    "availability": "https://schema.org/InStock",
                    "priceSpecification": {"priceType": "https://schema.org/ListPrice", "price": 149990},
                },
            }
        ),
        "<h1>Other heading</h1>",
    )
    o = extract_offer(html)
    assert (o.title, o.price, o.currency, o.mrp, o.in_stock, o.source) == (
        "Aurora 15 (RTX 5070)",
        129990.0,
        "₹",
        149990.0,
        True,
        "jsonld",
    )
    assert o.image == "https://img.example.com/a.jpg"


def test_jsonld_graph_and_aggregate_offer():
    doc = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebPage", "name": "ignored"},
            {"@type": ["Product"], "name": "Phone X", "offers": {"@type": "AggregateOffer", "lowPrice": 54999}},
        ],
    }
    o = extract_offer(page(ld(doc)))
    assert o.title == "Phone X" and o.price == 54999.0 and o.source == "jsonld"


def test_jsonld_list_of_documents_and_offers_prefers_in_stock():
    docs = [
        {"@type": "Organization", "name": "Shop"},
        {
            "@type": "Product",
            "name": "Kettle",
            "offers": [
                {"@type": "Offer", "price": 900, "priceCurrency": "INR", "availability": "OutOfStock"},
                {"@type": "Offer", "price": 1200, "priceCurrency": "INR", "availability": "InStock"},
            ],
        },
    ]
    o = extract_offer(page(ld(docs)))
    assert o.price == 1200.0 and o.in_stock is True


def test_jsonld_out_of_stock_and_usd():
    doc = {
        "@type": "Product",
        "name": "Mug",
        "offers": {"price": 12.5, "priceCurrency": "USD", "availability": "http://schema.org/OutOfStock"},
    }
    o = extract_offer(page(ld(doc)))
    assert (o.price, o.currency, o.in_stock) == (12.5, "$", False)


def test_malformed_jsonld_is_repaired_or_skipped():
    trailing_comma = '{"@type": "Product", "name": "Lamp", "offers": {"price": "1,499", "priceCurrency": "INR",},}'
    assert extract_offer(page(ld(trailing_comma, raw=True))).price == 1499.0
    broken = '{"@type": "Product", "name": "Lamp", "offers": {'
    html = page(ld(broken, raw=True) + "<meta property='product:price:amount' content='1299'>")
    o = extract_offer(html)
    assert o.price == 1299.0 and o.source == "meta"


def test_meta_tags_only():
    head = (
        "<meta property='og:title' content='Aurora 15 RTX 5070 Gaming Laptop'>"
        "<meta property='product:price:amount' content='124499'>"
        "<meta property='product:price:currency' content='INR'>"
        "<meta property='product:availability' content='in stock'>"
    )
    o = extract_offer(page(head, "<h1>Aurora</h1><p>MRP <del>₹1,39,990</del></p>"))
    assert (o.title, o.price, o.currency, o.in_stock, o.source, o.mrp) == (
        "Aurora 15 RTX 5070 Gaming Laptop",
        124499.0,
        "₹",
        True,
        "meta",
        139990.0,
    )


def test_itemprop_price():
    body = (
        "<div itemscope><span itemprop='price' content='799.00'>₹799</span>"
        "<meta itemprop='priceCurrency' content='INR'></div>"
    )
    o = extract_offer(page(body=body))
    assert o.price == 799.0 and o.currency == "₹" and o.source == "meta"


def test_heuristic_and_regex_fallbacks_and_stock_text():
    o = extract_offer(page(body="<h1>Desk</h1><span class='sale-price'>₹4,999</span><p>Out of stock</p>"))
    assert (o.price, o.source, o.in_stock, o.title) == (4999.0, "heuristic", False, "Desk")
    o = extract_offer(page(body="<h1>Chair</h1><p>Now only Rs. 2,499 — add to cart</p>"))
    assert (o.price, o.source, o.in_stock) == (2499.0, "regex", True)


def test_no_price_and_titles():
    o = extract_offer(page(body="<p>Nothing to see</p>"))
    assert o.price is None and o.title == "Fallback title"
    assert product_title(page("<meta property='og:title' content='OG name'>", "<h1>H1 name</h1>")) == "OG name"


def test_helpers():
    assert to_number("₹ 1,29,990") == 129990.0 and to_number(0) is None and to_number(True) is None
    assert availability_of("https://schema.org/PreOrder") is True and availability_of("SoldOut") is False
    assert availability_of("weird") is None
    assert looks_like_bot_wall("<p>Please complete the CAPTCHA</p>") and not looks_like_bot_wall("<p>hello</p>")
