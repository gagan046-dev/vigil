"""Turn a plain-English request into an ErrandSpec.

The rule-based parser handles the common phrasings deterministically (and is
the default in dev). The LLM is consulted only when the rules had to guess.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from pydantic import ValidationError

from .config import min_interval_minutes
from .extract import format_money, match_price_at_start
from .models import ALLOWED_INTERVALS, ErrandSpec, snap_interval


class PlanError(ValueError):
    """The request cannot be turned into a watch (e.g. no URL)."""


@dataclass
class PlanResult:
    spec: ErrandSpec
    source: str  # "llm" | "rules"
    notes: list[str] = field(default_factory=list)


@dataclass
class _Draft:
    data: dict
    notes: list[str]
    ambiguous: bool


URL_RE = re.compile(r"https?://[^\s<>\"'`]+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>'\""

_BELOW = re.compile(
    r"\b(?:drops?\s+(?:below|under|to)|falls?\s+(?:below|under|to)|goes\s+(?:below|under)|"
    r"dips?\s+(?:below|under)|below|under|less\s+than|cheaper\s+than|lower\s+than)\s*(?:of\s+)?",
    re.IGNORECASE,
)
_ABOVE = re.compile(
    r"\b(?:rises?\s+(?:above|over|to)|goes\s+(?:above|over)|above|over|more\s+than|"
    r"exceeds?|greater\s+than|higher\s+than)\s*(?:of\s+)?",
    re.IGNORECASE,
)
_QUOTED = re.compile(r"\"([^\"]{1,120})\"|“([^”]{1,120})”|‘([^’]{1,120})’|(?<![\w])'([^']{1,120})'(?!\w)")
_DISAPPEAR = re.compile(
    r"\b(disappears?|disappeared|gone|goes\s+away|removed|no\s+longer|vanish(?:es)?)\b", re.IGNORECASE
)
_APPEAR_PHRASES = [
    (re.compile(r"\bin[\s-]stock\b", re.I), "in stock"),
    (re.compile(r"\bslots?\s+available\b", re.I), None),  # keep the matched text
    (re.compile(r"\bavailable\b", re.I), "available"),
    (re.compile(r"\bslots?\b", re.I), "slot"),
]
_WHEN_APPEARS = re.compile(
    r"\bwhen\s+(?:the\s+|a\s+|an\s+)?(.{2,60}?)\s+(?:appears?|shows?\s+up|opens?)\b", re.IGNORECASE
)
_OPENS = re.compile(r"\b(\w+)\s+opens?\b", re.IGNORECASE)
_APPEARS_WORD = re.compile(r"\b(appears?|shows?\s+up|opens?)\b", re.IGNORECASE)
_CHANGE = re.compile(r"\b(change|changes|changed|update|updates|updated|new)\b", re.IGNORECASE)
_RENDER = re.compile(r"\b(javascript|js|dynamic|dynamically|client[-\s]side)\b", re.IGNORECASE)
_SELECTOR = re.compile(r"\bselector\s*[:=]?\s*[`\"']([^`\"']{1,300})[`\"']", re.IGNORECASE)

_FREQ_EVERY = re.compile(r"\bevery\s+(\d+(?:\.\d+)?)\s*(minutes?|mins?|m|hours?|hrs?|h|days?|d)\b", re.IGNORECASE)
_FREQ_FIXED = [
    (re.compile(r"\bevery\s+(?:single\s+)?minute\b", re.I), 1),
    (re.compile(r"\b(?:every\s+)?half\s+(?:an\s+)?hour\b|\bhalf-hourly\b", re.I), 30),
    (re.compile(r"\bevery\s+quarter\s+(?:of\s+an\s+)?hour\b", re.I), 15),
    (re.compile(r"\btwice\s+a\s+day\b", re.I), 720),
    (re.compile(r"\bhourly\b|\bevery\s+hour\b|\bonce\s+an\s+hour\b", re.I), 60),
    (re.compile(r"\bdaily\b|\bevery\s+day\b|\bevery\s+morning\b|\bonce\s+a\s+day\b", re.I), 1440),
]


def _first_url(text: str) -> str | None:
    m = URL_RE.search(text or "")
    if not m:
        return None
    return m.group(0).rstrip(_TRAILING)


def _parse_frequency(text: str) -> tuple[int | None, str]:
    """Return (minutes or None, text with the frequency phrase removed)."""
    m = _FREQ_EVERY.search(text)
    if m:
        n = float(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("h"):
            n *= 60
        elif unit.startswith("d"):
            n *= 1440
        return int(round(n)) or 1, text[: m.start()] + " " + text[m.end() :]
    for rx, minutes in _FREQ_FIXED:
        m = rx.search(text)
        if m:
            return minutes, text[: m.start()] + " " + text[m.end() :]
    return None, text


def _price_after(rx: re.Pattern, text: str):
    """Earliest comparator followed directly by a price. Returns (pos, PriceMatch)."""
    for m in rx.finditer(text):
        pm = match_price_at_start(text[m.end() :])
        if pm:
            return m.start(), pm
    return None


def _quoted(text: str) -> str | None:
    m = _QUOTED.search(text)
    if not m:
        return None
    return next(g for g in m.groups() if g is not None).strip() or None


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "page").lower()
    return host[4:] if host.startswith("www.") else host


def _auto_name(data: dict, currency: str | None) -> str:
    host = _host(data["url"])
    kind = data["kind"]
    if kind == "price_below":
        what = f"price below {format_money(data['threshold'], currency)}"
    elif kind == "price_above":
        what = f"price above {format_money(data['threshold'], currency)}"
    elif kind == "text_appears":
        what = f'"{data["keyword"]}" appears'
    elif kind == "text_disappears":
        what = f'"{data["keyword"]}" disappears'
    else:
        what = "page changes"
    name = f"{host}: {what}"
    return name if len(name) <= 80 else name[:77] + "..."


def _clamp_interval(minutes: int, notes: list[str]) -> int:
    snapped = snap_interval(minutes)
    if snapped != minutes:
        notes.append(f"Interval rounded to {snapped} minutes (allowed: {', '.join(map(str, ALLOWED_INTERVALS))}).")
    floor = min_interval_minutes()
    if snapped < floor:
        snapped = next(i for i in ALLOWED_INTERVALS if i >= floor)
        notes.append(f"Checking every {snapped} minutes — the minimum allowed here.")
    return snapped


_BEST = re.compile(
    r"\b(?:best|lowest|cheapest)\s+(?:price|deal|offer)s?\b|\bcompare\s+prices?\b|"
    r"\bacross\s+(?:all\s+)?(?:sellers|shops|stores|sites)\b",
    re.IGNORECASE,
)
_BEST_FILLER = re.compile(
    r"\b(?:tell|notify|alert|ping|let|show|give|find)\s+me\b|\b(?:let\s+me\s+)?know\b|\bwhen(?:ever)?\b|"
    r"\b(?:in\s+the\s+)?market\b|\bcurrently\b|\bavailable\b|\bany\s+(?:seller|shop|store)\b|"
    r"\b(?:the|of|for|on|is|at|me|please|watch|track|gets?|price|drops?|falls?|goes|to|a|an)\b",
    re.IGNORECASE,
)
BEST_PRICE_MIN_RECOMMENDED = 60


def _best_rules(text: str) -> _Draft:
    """'Tell me when the best price of Aurora 15 drops below ₹1,25,000' -> best_price_below."""
    notes: list[str] = []
    url = _first_url(text)
    rest = URL_RE.sub(" ", text)
    minutes, rest = _parse_frequency(rest)
    if minutes is None:
        minutes = BEST_PRICE_MIN_RECOMMENDED
        notes.append("No frequency given — checking every 60 minutes.")
    every = _clamp_interval(minutes, notes)
    if every < BEST_PRICE_MIN_RECOMMENDED:
        notes.append(
            "Each best-price check visits several seller sites; every 60 minutes or less often is "
            "recommended for real shops."
        )
    data: dict = {"kind": "best_price_below", "every_minutes": every, "url": url}
    below = _price_after(_BELOW, rest)
    if below:
        data["threshold"] = below[1].value
        rest = rest[: below[0]]
    else:
        notes.append("No target price given — you'll be told whenever the best price drops.")
    rest = _BEST.sub(" ", rest)
    rest = _BEST_FILLER.sub(" ", rest)
    query = re.sub(r"\s+", " ", re.sub(r"[?!,;:]+", " ", rest)).strip(" .'\"")
    if query:
        data["query"] = query[:300]
    elif not url:
        raise PlanError("Tell me which product to compare, e.g. 'best price of Apple iPhone 16 128GB'.")
    label = query or _host(url)
    name = f"Best price: {label}"
    data["name"] = name if len(name) <= 80 else name[:77] + "..."
    notes.append("Searches the allowed sellers on every check and alerts on the cheapest matching offer.")
    return _Draft(data, notes, False)


def _rules(text: str) -> _Draft:
    if _BEST.search(text or ""):
        return _best_rules(text)
    url = _first_url(text)
    if not url:
        raise PlanError("I couldn't find a web address (http:// or https://) in your request.")
    notes: list[str] = []
    ambiguous = False
    rest = URL_RE.sub(" ", text)

    minutes, rest = _parse_frequency(rest)
    if minutes is None:
        minutes = 60
        notes.append("No frequency given — checking every 60 minutes.")
    every = _clamp_interval(minutes, notes)

    data: dict = {"url": url, "every_minutes": every, "render": bool(_RENDER.search(rest))}
    if data["render"]:
        notes.append("You mentioned JavaScript/dynamic content — pages will be rendered in a browser.")
    sel = _SELECTOR.search(rest)
    if sel:
        data["selector"] = sel.group(1).strip()
        rest = rest[: sel.start()] + " " + rest[sel.end() :]

    currency = None
    below = _price_after(_BELOW, rest)
    above = _price_after(_ABOVE, rest)
    quoted = _quoted(rest)
    if below or above:
        if below and above:
            pos_kind = "price_below" if below[0] <= above[0] else "price_above"
        else:
            pos_kind = "price_below" if below else "price_above"
        pm = (below if pos_kind == "price_below" else above)[1]
        data.update(kind=pos_kind, threshold=pm.value)
        currency = pm.currency
        if not pm.currency:
            notes.append("No currency given — comparing the plain number on the page.")
    elif quoted and _DISAPPEAR.search(rest):
        data.update(kind="text_disappears", keyword=quoted)
    elif quoted:
        data.update(kind="text_appears", keyword=quoted)
    else:
        keyword = None
        for rx, kw in _APPEAR_PHRASES:
            m = rx.search(rest)
            if m:
                keyword = kw or re.sub(r"\s+", " ", m.group(0)).strip()
                break
        if keyword is None:
            m = _WHEN_APPEARS.search(rest)
            if m and not _CHANGE.search(m.group(1)):
                keyword = m.group(1).strip(" ,.")
                ambiguous = True
            elif _APPEARS_WORD.search(rest):
                m2 = _OPENS.search(rest)
                if m2 and m2.group(1).lower() not in ("it", "page", "site", "when"):
                    keyword = m2.group(1)
                    ambiguous = True
        if keyword and _DISAPPEAR.search(rest):
            data.update(kind="text_disappears", keyword=keyword[:120])
        elif keyword:
            data.update(kind="text_appears", keyword=keyword[:120])
            notes.append(f'Watching for the text "{keyword[:120]}" (case-insensitive).')
        elif _CHANGE.search(rest):
            data.update(kind="page_changed")
        else:
            data.update(kind="page_changed")
            ambiguous = True
            notes.append("No clear condition found — I'll tell you whenever the page changes.")

    data["name"] = _auto_name(data, currency)
    return _Draft(data, notes, ambiguous)


PLANNER_SYSTEM = (
    "You convert a user's request to watch a web page into strict JSON. Reply with ONE JSON object "
    "and nothing else, with keys: name (<=80 chars), url (must be copied from the request), kind "
    "(one of price_below, price_above, text_appears, text_disappears, page_changed, best_price_below), "
    "query (product name, only for best_price_below: the best price across many sellers; url is then "
    "optional), threshold (number, for price kinds, convert lakh/k to a plain number), keyword (text to look for, "
    "only for text kinds), selector (CSS selector or null), render (true if the page needs JavaScript), "
    "every_minutes (one of " + ", ".join(map(str, ALLOWED_INTERVALS)) + "), notes (list of short "
    "strings explaining assumptions)."
)


def _llm_plan(text: str, draft: _Draft, llm) -> PlanResult | None:
    reply = llm.chat_json(
        PLANNER_SYSTEM, f"Request: {text}\nRule-based guess: {json.dumps(draft.data)}", purpose="plan"
    )
    if not reply:
        return None
    notes = [str(n)[:200] for n in reply.get("notes") or [] if isinstance(n, str | int | float)][:5]
    data = {k: reply.get(k) for k in ErrandSpec.model_fields if reply.get(k) is not None}
    # Never trust a URL the model invented: it must appear in the user's text.
    if not isinstance(data.get("url"), str) or data["url"] not in text:
        data["url"] = draft.data["url"]
    data.setdefault("every_minutes", draft.data["every_minutes"])
    try:
        data["every_minutes"] = _clamp_interval(int(float(data["every_minutes"])), notes)
        spec = ErrandSpec(**data)
    except (ValidationError, ValueError, TypeError):
        return None
    return PlanResult(spec, "llm", notes)


def plan(text: str, llm=None) -> PlanResult:
    text = (text or "").strip()
    if not text:
        raise PlanError("Tell me what to watch, including the page's web address.")
    draft = _rules(text)
    if draft.ambiguous and llm is not None and llm.available():
        result = _llm_plan(text, draft, llm)
        if result:
            return result
        draft.notes.append("The AI planner was unavailable; used the rule-based parser.")
    try:
        spec = ErrandSpec(**draft.data)
    except ValidationError as exc:
        raise PlanError(exc.errors()[0].get("msg", "invalid request")) from exc
    return PlanResult(spec, "rules", draft.notes)
