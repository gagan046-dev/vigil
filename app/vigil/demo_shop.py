"""A safe local shop to watch, so tests and demos never scrape real sites.

Run as: uvicorn vigil.demo_shop:app --port 8000
"""

from __future__ import annotations

import json
import threading
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .extract import group_indian

PRODUCT = "Aurora 15 (RTX 5070)"
DEFAULT_PRICE = 134990.0

# Fictional sellers for the price-comparison demo (no real brands). Each one exposes its
# price differently so every extraction path is exercised:
#   pixelmart  JSON-LD Product/Offer           bazaar99  meta tags only
#   northwind  price filled in by JavaScript   bytebay   a *different* model (must be rejected)
#   quietcart  disallowed by robots.txt (must show up as blocked)
DEFAULT_SELLERS = {
    "pixelmart": {"price": 129990.0, "mrp": 149990.0, "in_stock": True},
    "bazaar99": {"price": 124499.0, "mrp": 139990.0, "in_stock": True},
    "northwind": {"price": 131000.0, "mrp": None, "in_stock": True},
    "bytebay": {"price": 189990.0, "mrp": 199990.0, "in_stock": True},
    "quietcart": {"price": 119990.0, "mrp": None, "in_stock": True},
}

_lock = threading.Lock()
_state: dict = {}


def reset_state() -> dict:
    with _lock:
        _state.clear()
        _state.update(
            price=DEFAULT_PRICE,
            in_stock=False,
            layout=1,
            notices=["Welcome to the Aurora store.", "Free shipping on orders over ₹50,000."],
            sellers={k: dict(v) for k, v in DEFAULT_SELLERS.items()},
        )
        return dict(_state)


reset_state()


def rupees(value: float) -> str:
    return "₹" + group_indian(int(round(value)))


app = FastAPI(title="Vigil demo shop", docs_url=None, redoc_url=None, openapi_url=None)

_STYLE = """
body{font-family:Georgia,serif;max-width:46rem;margin:2rem auto;padding:0 1rem;color:#1d2433;background:#faf8f3}
h1{font-size:1.6rem}.stock{font-family:monospace}nav a{margin-right:1rem}
.price-now,.pdp-amount b{font-size:1.8rem;font-family:monospace}
"""

_FILLER = (
    "<p>The Aurora 15 is a 15.6-inch gaming laptop with an RTX 5070 graphics card, a 240 Hz display, "
    "32 GB of memory and a 1 TB SSD. This page is part of the Vigil demo and is safe to watch as often "
    "as you like; prices and stock are controlled from the admin panel.</p>"
)


def _page(title: str, body: str) -> HTMLResponse:
    html = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(title)}</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_STYLE}</style>"
        f"</head><body><nav><a href='/'>Shop</a> <a href='/static'>Static</a> <a href='/notices'>Notices</a> "
        f"<a href='/sellers'>Sellers</a> <a href='/admin'>Admin</a></nav>{body}</body></html>"
    )
    return HTMLResponse(html)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nDisallow: /sellers/quietcart/\nAllow: /\n"


@app.get("/api/product")
def product() -> dict:
    with _lock:
        return {
            "name": PRODUCT,
            "price": _state["price"],
            "price_text": rupees(_state["price"]),
            "currency": "INR",
            "in_stock": _state["in_stock"],
            "layout": _state["layout"],
        }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    # The price and stock are filled in by JavaScript: a plain HTTP fetch only sees "Loading…".
    body = (
        f"<h1>{escape(PRODUCT)}</h1>"
        "<p>Price: <span class='price-now' id='price'>Loading…</span></p>"
        "<p class='stock' id='stock'>Loading…</p>"
        "<script>"
        "fetch('/api/product').then(r=>r.json()).then(p=>{"
        "document.getElementById('price').textContent=p.price_text;"
        "document.getElementById('stock').textContent=p.in_stock?'In stock':'Out of stock';"
        "document.getElementById('details').innerHTML=" + repr(_FILLER) + ";});"
        "</script><div id='details'></div>"
    )
    return _page(PRODUCT, body)


@app.get("/static", response_class=HTMLResponse)
def static_page() -> HTMLResponse:
    with _lock:
        price, in_stock, layout = _state["price"], _state["in_stock"], _state["layout"]
    if layout == 1:
        price_html = f"<p>Price: <span class='price-now'>{rupees(price)}</span></p>"
    else:
        price_html = f"<section class='buy-box'><div class='pdp-amount'><b>{rupees(price)}</b></div></section>"
    stock = "In stock" if in_stock else "Out of stock"
    body = f"<h1>{escape(PRODUCT)}</h1>{price_html}<p class='stock'>{stock}</p>{_FILLER}"
    return _page(PRODUCT, body)


@app.get("/notices", response_class=HTMLResponse)
def notices() -> HTMLResponse:
    with _lock:
        items = list(_state["notices"])
    lis = "".join(f"<li>{escape(n)}</li>" for n in items)
    return _page("Notice board", f"<h1>Notice board</h1><ul id='notices'>{lis}</ul>")


_ADMIN = """
<h1>Demo shop admin</h1>
<p>Change the shop, then watch Vigil notice.</p>
<form id="f-price"><label>Price (INR) <input name="value" type="number" min="1" value="114990"></label>
<button>Set price</button></form>
<p><button data-stock="true">Mark in stock</button> <button data-stock="false">Mark out of stock</button></p>
<p><button data-layout="1">Layout 1</button> <button data-layout="2">Layout 2</button></p>
<form id="f-notice"><label>Notice <input name="text" maxlength="200" value="Slot available"></label>
<button>Post notice</button></form>
<form id="f-seller"><label>Seller <select name="key"><option>pixelmart</option><option>bazaar99</option>
<option>northwind</option><option>bytebay</option><option>quietcart</option></select></label>
<label>Price (INR) <input name="price" type="number" min="1" value="119990"></label>
<button>Set seller price</button></form>
<p><button id="reset">Reset everything</button></p>
<pre id="state"></pre>
<script>
const post=(path,data)=>fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify(data||{})}).then(r=>r.json()).then(s=>{document.getElementById('state').textContent=
  JSON.stringify(s,null,2)});
document.getElementById('f-price').onsubmit=e=>{e.preventDefault();
  post('/admin/price',{value:Number(e.target.value.value)})};
document.getElementById('f-notice').onsubmit=e=>{e.preventDefault();post('/admin/notice',{text:e.target.text.value})};
document.querySelectorAll('[data-stock]').forEach(b=>b.onclick=()=>post('/admin/stock',
  {in_stock:b.dataset.stock==='true'}));
document.querySelectorAll('[data-layout]').forEach(b=>b.onclick=()=>post('/admin/layout',
  {layout:Number(b.dataset.layout)}));
document.getElementById('f-seller').onsubmit=e=>{e.preventDefault();
  post('/admin/sellers/'+e.target.key.value,{price:Number(e.target.price.value)})};
document.getElementById('reset').onclick=()=>post('/admin/reset');
fetch('/admin/state').then(r=>r.json()).then(s=>{document.getElementById('state').textContent=
  JSON.stringify(s,null,2)});
</script>
"""


@app.get("/admin", response_class=HTMLResponse)
def admin() -> HTMLResponse:
    return _page("Demo shop admin", _ADMIN)


class PriceIn(BaseModel):
    value: float = Field(gt=0, lt=1e9)


class StockIn(BaseModel):
    in_stock: bool


class LayoutIn(BaseModel):
    layout: int = Field(ge=1, le=2)


class NoticeIn(BaseModel):
    text: str = Field(min_length=1, max_length=200)


def _snapshot() -> dict:
    with _lock:
        return dict(
            _state,
            notices=list(_state["notices"]),
            sellers={k: dict(v) for k, v in _state["sellers"].items()},
        )


@app.get("/admin/state")
def admin_state() -> dict:
    return _snapshot()


@app.post("/admin/price")
def set_price(body: PriceIn) -> dict:
    with _lock:
        _state["price"] = body.value
    return _snapshot()


@app.post("/admin/stock")
def set_stock(body: StockIn) -> dict:
    with _lock:
        _state["in_stock"] = body.in_stock
    return _snapshot()


@app.post("/admin/layout")
def set_layout(body: LayoutIn) -> dict:
    with _lock:
        _state["layout"] = body.layout
    return _snapshot()


@app.post("/admin/notice")
def add_notice(body: NoticeIn) -> dict:
    with _lock:
        _state["notices"] = [body.text, *_state["notices"]][:20]
    return _snapshot()


@app.post("/admin/reset")
def reset() -> dict:
    reset_state()
    return _snapshot()


# ---------------------------------------------------------------- demo sellers


def _seller(key: str) -> dict:
    with _lock:
        return dict(_state["sellers"][key])


def _stock_text(in_stock: bool) -> str:
    return "In stock" if in_stock else "Out of stock"


def _seller_page(title: str, shop: str, head: str, body: str) -> HTMLResponse:
    html = (
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>{escape(title)} | {escape(shop)}</title>"
        f"<meta name='viewport' content='width=device-width,initial-scale=1'><style>{_STYLE}</style>{head}"
        f"</head><body><nav><strong>{escape(shop)}</strong> (fictional demo seller)</nav>{body}{_FILLER}</body></html>"
    )
    return HTMLResponse(html)


def _ld(data: dict) -> str:
    # "</" must not end the script element early
    return "<script type='application/ld+json'>" + json.dumps(data).replace("</", "<\\/") + "</script>"


@app.get("/sellers/pixelmart/aurora-15", response_class=HTMLResponse)
def seller_pixelmart() -> HTMLResponse:
    s = _seller("pixelmart")
    offer = {
        "@type": "Offer",
        "price": f"{s['price']:.2f}",
        "priceCurrency": "INR",
        "availability": "https://schema.org/" + ("InStock" if s["in_stock"] else "OutOfStock"),
    }
    if s.get("mrp"):
        offer["priceSpecification"] = {
            "@type": "UnitPriceSpecification",
            "priceType": "https://schema.org/ListPrice",
            "price": s["mrp"],
            "priceCurrency": "INR",
        }
    ld = _ld(
        {
            "@context": "https://schema.org",
            "@graph": [
                {"@type": "BreadcrumbList", "itemListElement": []},
                {
                    "@type": "Product",
                    "name": "Aurora 15 (RTX 5070) Gaming Laptop",
                    "sku": "AUR15-5070",
                    "offers": offer,
                },
            ],
        }
    )
    body = (
        "<h1>Aurora 15 (RTX 5070) Gaming Laptop</h1>"
        f"<p>Deal price: <span class='deal'>{rupees(s['price'])}</span></p>"
        f"<p class='stock'>{_stock_text(s['in_stock'])}</p>"
    )
    return _seller_page("Aurora 15 (RTX 5070)", "PixelMart", ld, body)


@app.get("/sellers/bazaar99/aurora-15", response_class=HTMLResponse)
def seller_bazaar99() -> HTMLResponse:
    s = _seller("bazaar99")
    head = (
        "<meta property='og:title' content='Aurora 15 RTX 5070 15.6\" Gaming Laptop'>"
        f"<meta property='product:price:amount' content='{s['price']:.0f}'>"
        "<meta property='product:price:currency' content='INR'>"
        f"<meta property='product:availability' content='{'in stock' if s['in_stock'] else 'out of stock'}'>"
    )
    mrp = f"<p>MRP <del>{rupees(s['mrp'])}</del></p>" if s.get("mrp") else ""
    body = (
        '<h1>Aurora 15 RTX 5070 15.6" Gaming Laptop</h1>'
        f"<p>Our price today {rupees(s['price'])}</p>{mrp}"
        f"<p>{_stock_text(s['in_stock'])}</p>"
    )
    return _seller_page("Aurora 15 RTX 5070", "Bazaar99", head, body)


@app.get("/sellers/northwind/api")
def seller_northwind_api() -> dict:
    s = _seller("northwind")
    return {"price_text": rupees(s["price"]), "stock": _stock_text(s["in_stock"])}


@app.get("/sellers/northwind/aurora-15", response_class=HTMLResponse)
def seller_northwind() -> HTMLResponse:
    # A plain HTTP fetch only sees "Loading…": the price needs a browser.
    body = (
        "<h1>Aurora 15 Gaming Laptop, RTX 5070</h1>"
        "<p>Price: <span class='price' id='nw-price'>Loading…</span></p>"
        "<p id='nw-stock'>Loading…</p>"
        "<script>fetch('/sellers/northwind/api').then(r=>r.json()).then(p=>{"
        "document.getElementById('nw-price').textContent=p.price_text;"
        "document.getElementById('nw-stock').textContent=p.stock;});</script>"
    )
    return _seller_page("Aurora 15 Gaming Laptop", "Northwind Electronics", "", body)


@app.get("/sellers/bytebay/aurora-15-pro", response_class=HTMLResponse)
def seller_bytebay() -> HTMLResponse:
    s = _seller("bytebay")
    ld = _ld(
        {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": "Aurora 15 Pro RTX 5080 Gaming Laptop",
            "offers": {
                "@type": "AggregateOffer",
                "lowPrice": s["price"],
                "highPrice": s["mrp"] or s["price"],
                "priceCurrency": "INR",
                "offerCount": 2,
            },
        }
    )
    body = f"<h1>Aurora 15 Pro RTX 5080 Gaming Laptop</h1><p>From {rupees(s['price'])}</p>"
    return _seller_page("Aurora 15 Pro RTX 5080", "ByteBay", ld, body)


@app.get("/sellers/quietcart/aurora-15", response_class=HTMLResponse)
def seller_quietcart() -> HTMLResponse:
    # robots.txt disallows this path, so Vigil must never fetch it.
    s = _seller("quietcart")
    body = f"<h1>Aurora 15 RTX 5070</h1><p>Price: <span class='price'>{rupees(s['price'])}</span></p>"
    return _seller_page("Aurora 15 RTX 5070", "QuietCart", "", body)


class SellerIn(BaseModel):
    price: float | None = Field(default=None, gt=0, lt=1e9)
    in_stock: bool | None = None


@app.post("/admin/sellers/{key}")
def set_seller(key: str, body: SellerIn) -> dict:
    with _lock:
        if key not in _state["sellers"]:
            raise HTTPException(404, "unknown seller")
        if body.price is not None:
            _state["sellers"][key]["price"] = body.price
        if body.in_stock is not None:
            _state["sellers"][key]["in_stock"] = body.in_stock
    return _snapshot()


@app.get("/sellers", response_class=HTMLResponse)
def sellers_index() -> HTMLResponse:
    from .search import DEMO_SELLERS

    items = "".join(f"<li><a href='{path}'>{escape(name)}</a></li>" for name, path in DEMO_SELLERS.values())
    return _page("Demo sellers", f"<h1>Demo sellers</h1><p>Fictional shops for price comparison.</p><ul>{items}</ul>")
