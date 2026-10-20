"""A safe local shop to watch, so tests and demos never scrape real sites.

Run as: uvicorn vigil.demo_shop:app --port 8000
"""

from __future__ import annotations

import threading
from html import escape

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .extract import group_indian

PRODUCT = "Aurora 15 (RTX 5070)"
DEFAULT_PRICE = 134990.0

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
        f"<a href='/admin'>Admin</a></nav>{body}</body></html>"
    )
    return HTMLResponse(html)


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nAllow: /\n"


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
        return dict(_state, notices=list(_state["notices"]))


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
