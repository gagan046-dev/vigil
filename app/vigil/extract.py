"""Deterministic page understanding: visible text, prices and CSS selectors.

Everything here is pure (no network, no LLM) so it is cheap and easy to test.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

_DROP_TAGS = ("script", "style", "noscript", "template", "svg")
_WS = re.compile(r"\s+")

# ---------------------------------------------------------------- prices

_CUR = r"(?P<cur>₹|(?<![a-z])(?:rs\.?|inr|usd|eur|gbp)|us\$|\$|€|£)"
_NUM = r"(?<![\d.,])(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?![\d])"
_MULT = r"(?:\s*(?P<mult>lakhs?|lacs?|lac|crores?|cr|k|thousand|million|mn)\b)?"
PRICE_RE = re.compile(rf"(?:{_CUR}\s*)?{_NUM}{_MULT}", re.IGNORECASE)

_MULTIPLIERS = {
    "lakh": 1e5,
    "lakhs": 1e5,
    "lac": 1e5,
    "lacs": 1e5,
    "crore": 1e7,
    "crores": 1e7,
    "cr": 1e7,
    "k": 1e3,
    "thousand": 1e3,
    "million": 1e6,
    "mn": 1e6,
}
_CURRENCY = {
    "₹": "₹",
    "rs": "₹",
    "rs.": "₹",
    "inr": "₹",
    "$": "$",
    "us$": "$",
    "usd": "$",
    "€": "€",
    "eur": "€",
    "£": "£",
    "gbp": "£",
}


@dataclass
class PriceMatch:
    value: float
    currency: str | None
    text: str


def _to_match(m: re.Match) -> PriceMatch | None:
    try:
        value = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    mult = (m.group("mult") or "").lower()
    value *= _MULTIPLIERS.get(mult, 1)
    cur = m.group("cur")
    currency = _CURRENCY.get(cur.lower()) if cur else None
    if value <= 0:
        return None
    return PriceMatch(round(value, 2), currency, m.group(0).strip())


def find_price(text: str, require_currency: bool = False) -> PriceMatch | None:
    """First price-like number in text (optionally only currency-prefixed ones)."""
    for m in PRICE_RE.finditer(text or ""):
        pm = _to_match(m)
        if pm is None:
            continue
        if require_currency and not pm.currency:
            continue
        return pm
    return None


def parse_price(text: str) -> float | None:
    pm = find_price(text)
    return pm.value if pm else None


def match_price_at_start(text: str) -> PriceMatch | None:
    m = PRICE_RE.match(text or "")
    return _to_match(m) if m else None


def group_indian(n: int) -> str:
    s = str(abs(n))
    if len(s) <= 3:
        out = s
    else:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        out = ",".join(groups) + "," + tail
    return ("-" if n < 0 else "") + out


def format_number(value: float, currency: str | None = None) -> str:
    """₹ / unknown currency -> Indian grouping (1,20,000); others -> 120,000."""
    value = round(float(value), 2)  # 9.999 -> 10.00, not 9 + ".00"
    whole = int(value)
    frac = round(value - whole, 2)
    body = group_indian(whole) if currency in (None, "", "₹") else f"{whole:,}"
    if frac:
        body += f"{frac:.2f}"[1:]
    return body


def format_money(value: float | None, currency: str | None = None) -> str:
    if value is None:
        return "-"
    return f"{currency or ''}{format_number(value, currency)}"


# ---------------------------------------------------------------- text


def parse_html(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    return soup


_BLOCK_TAGS = frozenset(
    [
        "p",
        "div",
        "section",
        "article",
        "header",
        "footer",
        "nav",
        "aside",
        "main",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "ul",
        "ol",
        "dl",
        "dt",
        "dd",
        "table",
        "tr",
        "td",
        "th",
        "thead",
        "tbody",
        "tfoot",
        "caption",
        "form",
        "fieldset",
        "legend",
        "blockquote",
        "pre",
        "figure",
        "figcaption",
        "address",
        "hr",
        "br",
        "details",
        "summary",
        "option",
        "body",
        "html",
    ]
)


def _lines_of(el: Tag) -> list[str]:
    """Visible text split into lines at block-element boundaries (inline text is joined)."""
    lines: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        line = _WS.sub(" ", "".join(buf)).strip()
        if line:
            lines.append(line)
        buf.clear()

    stack: list = [iter(el.children)]
    closing: list[bool] = []
    while stack:
        child = next(stack[-1], None)
        if child is None:
            stack.pop()
            if closing and closing.pop():
                flush()
            continue
        if isinstance(child, Tag):
            block = child.name in _BLOCK_TAGS
            if block:
                flush()
            stack.append(iter(child.children))
            closing.append(block)
        elif type(child) is NavigableString:  # skips comments, doctypes, CDATA
            buf.append(str(child))
    flush()
    return lines


def safe_select(soup: BeautifulSoup, selector: str | None) -> list[Tag]:
    if not selector:
        return []
    try:
        return list(soup.select(selector))
    except Exception:  # invalid CSS from a user or the LLM
        return []


def page_lines(soup: BeautifulSoup, selector: str | None = None) -> tuple[list[str], bool]:
    """Visible text lines of the selector (if it matches) else of <body>.

    Returns (lines, selector_matched).
    """
    matched = safe_select(soup, selector)
    if matched:
        lines: list[str] = []
        for el in matched:
            lines.extend(_lines_of(el))
        return lines, True
    root = soup.body or soup
    return _lines_of(root), False


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def page_label(soup: BeautifulSoup) -> str | None:
    """A short human label for the page: first <h1>, else <title>."""
    for el in (soup.find("h1"), soup.find("title")):
        if el:
            txt = _WS.sub(" ", el.get_text(" ")).strip()
            if txt:
                return txt[:60]
    return None


# ---------------------------------------------------------------- price elements

_PRICE_ATTR = re.compile(r"price|amount", re.IGNORECASE)
_SKIP_ATTR = re.compile(r"old|was|mrp|strike|original|list-?price|regular|compare|before|save|discount", re.IGNORECASE)


def element_price(el: Tag) -> PriceMatch | None:
    if el.name == "meta":
        return find_price(el.get("content") or "")
    txt = _WS.sub(" ", el.get_text(" ")).strip()
    if not txt or len(txt) > 80:
        return None
    return find_price(txt)


def select_price(soup: BeautifulSoup, selector: str | None) -> tuple[PriceMatch, Tag] | None:
    for el in safe_select(soup, selector):
        pm = element_price(el)
        if pm:
            return pm, el
    return None


def _attr_blob(el: Tag) -> str:
    classes = el.get("class") or []
    if isinstance(classes, str):
        classes = [classes]
    return " ".join([el.get("id") or "", " ".join(classes), el.get("itemprop") or ""])


def heuristic_price(soup: BeautifulSoup) -> tuple[PriceMatch, Tag] | None:
    """Elements whose class/id/itemprop mentions price/amount and parse to a number."""
    candidates: list[tuple[PriceMatch, Tag]] = []
    for el in soup.find_all(True):
        blob = _attr_blob(el)
        if not _PRICE_ATTR.search(blob) or _SKIP_ATTR.search(blob):
            continue
        pm = element_price(el)
        if pm:
            candidates.append((pm, el))
    if not candidates:
        return None
    # Prefer the most specific element: drop candidates that contain another candidate.
    els = [c[1] for c in candidates]
    specific = [c for c in candidates if not any(o is not c[1] and any(p is c[1] for p in o.parents) for o in els)]
    pool = specific or candidates
    with_currency = [c for c in pool if c[0].currency]
    return (with_currency or pool)[0]


def find_element_containing(soup: BeautifulSoup, needle: str) -> Tag | None:
    """Smallest element whose visible text contains needle."""
    needle = _WS.sub(" ", needle or "").strip()
    if not needle:
        return None
    best: Tag | None = None
    best_len = 10**9
    for i, el in enumerate(soup.find_all(True)):
        if i > 5000:
            break
        if el.name in ("html", "body"):
            continue
        txt = _WS.sub(" ", el.get_text(" ")).strip()
        if needle in txt and len(txt) <= best_len:
            best, best_len = el, len(txt)
    return best


_SAFE_IDENT = re.compile(r"-?[A-Za-z_][\w-]*")


def _selector_part(el: Tag) -> str:
    idv = el.get("id")
    if isinstance(idv, str) and _SAFE_IDENT.fullmatch(idv):
        return f"{el.name}#{idv}"
    classes = el.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    safe = [c for c in classes if _SAFE_IDENT.fullmatch(c)]
    return el.name + "".join(f".{c}" for c in safe)


def css_selector_for(el: Tag, soup: BeautifulSoup) -> str | None:
    """tag + #id or .classes, adding up to 3 ancestor levels until unique.

    The result is verified by re-selecting it; None if no unique selector exists.
    """
    parts: list[str] = []
    node: Tag | None = el
    for _ in range(4):  # the element itself + up to 3 ancestors
        if node is None or not isinstance(node, Tag) or node.name in ("[document]",):
            break
        parts.insert(0, _selector_part(node))
        selector = " > ".join(parts)
        found = safe_select(soup, selector)
        if len(found) == 1 and found[0] is el:
            return selector
        node = node.parent
    return None
