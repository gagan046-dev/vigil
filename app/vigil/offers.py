"""Pull one product offer (title, price, MRP, stock) out of a seller's product page.

Order of trust, first hit wins for the price:
  1. JSON-LD (schema.org Product / Offer / AggregateOffer, @graph, lists)
  2. meta tags (product:price:amount, og:price:amount, itemprop=price)
  3. the existing price heuristics (price-ish class names, then currency regex)
The LLM fallback lives in compare.py (it needs the budget-aware client).

Everything here is pure: no network, no LLM.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass

from bs4 import BeautifulSoup, Tag

from .extract import find_price, heuristic_price, page_lines, parse_html

_WS = re.compile(r"\s+")
_TRAILING_COMMA = re.compile(r",\s*([}\]])")

CURRENCY_CODES = {"INR": "₹", "USD": "$", "EUR": "€", "GBP": "£"}

_OUT_OF_STOCK = re.compile(
    r"\b(out\s+of\s+stock|sold\s+out|currently\s+unavailable|not\s+available|coming\s+soon|notify\s+me)\b",
    re.IGNORECASE,
)
_IN_STOCK = re.compile(r"\b(in\s+stock|add\s+to\s+cart|add\s+to\s+bag|buy\s+now)\b", re.IGNORECASE)
_MRP_ATTR = re.compile(r"mrp|strike|was-?price|old-?price|list-?price|original|regular", re.IGNORECASE)
_BOT_WALL = re.compile(
    r"captcha|are\s+you\s+a\s+robot|verify\s+you\s+are\s+(?:a\s+)?human|access\s+denied|cf-challenge|"
    r"unusual\s+traffic|bot\s+protection|press\s+&\s+hold",
    re.IGNORECASE,
)


@dataclass
class OfferData:
    title: str | None = None
    price: float | None = None
    currency: str | None = None
    mrp: float | None = None
    in_stock: bool | None = None
    image: str | None = None
    source: str | None = None  # jsonld | meta | heuristic | regex | llm

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers


def _clean(text) -> str | None:
    if not isinstance(text, str):
        return None
    out = _WS.sub(" ", text).strip()
    return out[:300] or None


def to_number(raw) -> float | None:
    """'1,29,990.00' / 129990 / '₹ 1,29,990' -> 129990.0 (None if not a positive number)."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw) if raw > 0 else None
    if isinstance(raw, str):
        pm = find_price(raw)
        return pm.value if pm else None
    return None


def currency_symbol(code) -> str | None:
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip()
    return CURRENCY_CODES.get(code.upper(), code if len(code) <= 3 else None)


def availability_of(raw) -> bool | None:
    """schema.org availability (URL or bare word) -> True / False / None."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    word = raw.strip().rstrip("/").rsplit("/", 1)[-1].lower().replace(" ", "")
    if word in ("instock", "onlineonly", "limitedavailability", "instoreonly", "preorder", "presale", "in_stock"):
        return True
    if word in ("outofstock", "soldout", "discontinued", "backorder", "out_of_stock", "oos"):
        return False
    return None


# ---------------------------------------------------------------- JSON-LD


def _load_json(raw: str):
    raw = (raw or "").strip()
    if raw.startswith("<!--"):
        raw = raw[4:]
    if raw.endswith("-->"):
        raw = raw[:-3]
    for candidate in (raw, _TRAILING_COMMA.sub(r"\1", raw)):
        try:
            return json.loads(candidate, strict=False)
        except (ValueError, TypeError):
            continue
    return None


def _walk(node, depth: int = 0):
    """Yield every dict inside a JSON-LD document (handles @graph and nested lists)."""
    if depth > 8:
        return
    if isinstance(node, list):
        for item in node:
            yield from _walk(item, depth + 1)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "mainEntity", "itemListElement", "item"):
            if key in node:
                yield from _walk(node[key], depth + 1)


def _has_type(node: dict, *names: str) -> bool:
    t = node.get("@type")
    types = t if isinstance(t, list) else [t]
    wanted = {n.lower() for n in names}
    return any(isinstance(x, str) and x.rsplit("/", 1)[-1].lower() in wanted for x in types)


def _list_price(offer: dict) -> float | None:
    specs = offer.get("priceSpecification")
    for spec in specs if isinstance(specs, list) else [specs]:
        if isinstance(spec, dict):
            kind = str(spec.get("priceType") or "").lower()
            if "listprice" in kind or "srp" in kind or "msrp" in kind:
                return to_number(spec.get("price"))
    return None


def _offer_values(offers) -> tuple[float | None, str | None, bool | None, float | None]:
    """(price, currency, in_stock, mrp) from schema.org offers (Offer / AggregateOffer / list)."""
    best: tuple[float | None, str | None, bool | None, float | None] = (None, None, None, None)
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        if _has_type(offer, "AggregateOffer"):
            price = to_number(offer.get("lowPrice")) or to_number(offer.get("price"))
            nested = offer.get("offers")
            if price is None and nested is not None:
                return _offer_values(nested)
        else:
            price = to_number(offer.get("price"))
            if price is None:
                spec = offer.get("priceSpecification")
                for s in spec if isinstance(spec, list) else [spec]:
                    if isinstance(s, dict) and price is None and "listprice" not in str(s.get("priceType")).lower():
                        price = to_number(s.get("price"))
                        offer = {**offer, "priceCurrency": offer.get("priceCurrency") or s.get("priceCurrency")}
        if price is None:
            continue
        cur = currency_symbol(offer.get("priceCurrency"))
        stock = availability_of(offer.get("availability"))
        mrp = _list_price(offer)
        # several offers: prefer an in-stock one, then the cheapest
        if best[0] is None or (stock is not False and (best[2] is False or price < best[0])):
            best = (price, cur, stock, mrp)
    return best


def _image_of(node: dict) -> str | None:
    img = node.get("image")
    if isinstance(img, list):
        img = img[0] if img else None
    if isinstance(img, dict):
        img = img.get("url") or img.get("contentUrl")
    return img[:1000] if isinstance(img, str) and img.startswith(("http://", "https://")) else None


def from_jsonld(raw_soup: BeautifulSoup) -> OfferData | None:
    """First schema.org Product with a usable offer (or at least a name)."""
    fallback: OfferData | None = None
    for script in raw_soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)}):
        doc = _load_json(script.string or script.get_text() or "")
        if doc is None:
            continue
        for node in _walk(doc):
            if not _has_type(node, "Product", "ProductGroup", "IndividualProduct", "ProductModel"):
                continue
            offers = node.get("offers")
            if offers is None and isinstance(node.get("hasVariant"), list):
                offers = [v.get("offers") for v in node["hasVariant"] if isinstance(v, dict)]
                offers = [o for o in offers if o]
            price, cur, stock, mrp = _offer_values(offers) if offers else (None, None, None, None)
            data = OfferData(
                title=_clean(node.get("name")),
                price=price,
                currency=cur,
                mrp=mrp,
                in_stock=stock,
                image=_image_of(node),
                source="jsonld" if price is not None else None,
            )
            if price is not None:
                return data
            fallback = fallback or data
    return fallback


# ---------------------------------------------------------------- meta tags


def _meta(raw_soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        el = raw_soup.find("meta", attrs={"property": name}) or raw_soup.find("meta", attrs={"name": name})
        if el and isinstance(el.get("content"), str) and el["content"].strip():
            return el["content"].strip()
    return None


def from_meta(raw_soup: BeautifulSoup) -> OfferData:
    out = OfferData(
        title=_clean(_meta(raw_soup, "og:title", "twitter:title")),
        image=_meta(raw_soup, "og:image", "twitter:image"),
    )
    if out.image and not out.image.startswith(("http://", "https://")):
        out.image = None
    amount = _meta(raw_soup, "product:price:amount", "og:price:amount", "product:sale_price:amount")
    currency = _meta(raw_soup, "product:price:currency", "og:price:currency")
    stock = _meta(raw_soup, "product:availability", "og:availability")
    if amount is None:
        el = raw_soup.find(attrs={"itemprop": "price"})
        if isinstance(el, Tag):
            amount = el.get("content") or _clean(el.get_text(" "))
            cur_el = raw_soup.find(attrs={"itemprop": "priceCurrency"})
            if isinstance(cur_el, Tag):
                currency = currency or cur_el.get("content") or _clean(cur_el.get_text(" "))
    if stock is None:
        el = raw_soup.find(attrs={"itemprop": "availability"})
        if isinstance(el, Tag):
            stock = el.get("href") or el.get("content") or _clean(el.get_text(" "))
    price = to_number(amount)
    if price is not None:
        out.price = price
        out.currency = currency_symbol(currency) or (find_price(amount).currency if find_price(amount) else None)
        out.source = "meta"
    out.in_stock = availability_of(stock)
    return out


# ---------------------------------------------------------------- visible page


def _first_h1(soup: BeautifulSoup) -> str | None:
    el = soup.find("h1")
    return _clean(el.get_text(" ")) if el else None


def _mrp_from_page(soup: BeautifulSoup, price: float | None) -> float | None:
    for el in soup.find_all(True):
        blob = " ".join([el.get("id") or "", " ".join(el.get("class") or []), el.name])
        if el.name in ("del", "s", "strike") or _MRP_ATTR.search(blob):
            txt = _WS.sub(" ", el.get_text(" ")).strip()
            if not txt or len(txt) > 60:
                continue
            pm = find_price(txt)
            if pm and (price is None or pm.value > price):
                return pm.value
    return None


def stock_from_text(text: str) -> bool | None:
    if _OUT_OF_STOCK.search(text or ""):
        return False
    if _IN_STOCK.search(text or ""):
        return True
    return None


def looks_like_bot_wall(html: str) -> bool:
    return bool(_BOT_WALL.search((html or "")[:200_000]))


def extract_offer(html: str) -> OfferData:
    """Best-effort offer from a product page. price is None when nothing was found."""
    raw = BeautifulSoup(html or "", "html.parser")
    ld = from_jsonld(raw)
    meta = from_meta(raw)
    soup = parse_html(html)
    lines, _ = page_lines(soup)
    flat = " ".join(lines)

    out = OfferData()
    if ld and ld.price is not None:
        out = ld
    elif meta.price is not None:
        out = OfferData(meta.title, meta.price, meta.currency, None, meta.in_stock, meta.image, "meta")
    else:
        hit = heuristic_price(soup)
        if hit:
            out.price, out.currency, out.source = hit[0].value, hit[0].currency, "heuristic"
        else:
            pm = find_price(flat, require_currency=True)
            if pm:
                out.price, out.currency, out.source = pm.value, pm.currency, "regex"

    out.title = (ld.title if ld else None) or meta.title or _first_h1(soup) or out.title
    if not out.title:
        t = raw.find("title")
        out.title = _clean(t.get_text(" ")) if t else None
    out.image = out.image or (ld.image if ld else None) or meta.image
    if out.in_stock is None:
        out.in_stock = meta.in_stock if meta.in_stock is not None else stock_from_text(flat)
    if out.mrp is None and out.price is not None:
        out.mrp = _mrp_from_page(soup, out.price)
    if out.mrp is not None and out.price is not None and out.mrp <= out.price:
        out.mrp = None
    return out


def product_title(html: str) -> str | None:
    """JSON-LD Product.name > og:title > h1 > <title>."""
    return extract_offer(html).title
