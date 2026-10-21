"""Vigil UI mock server: serves frontend/ and fakes every /api endpoint the UI uses.

Standard library only. For UI development and screenshots, never deployed (the Dockerfile
does not copy dev/).

    python frontend/dev/mock-server.py [--port 8099] [--empty]

--empty starts with no watches, alerts or comparisons (to review empty states).
"""

from __future__ import annotations

import argparse
import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
SHOP = "http://vigil-demo-shop:8000"
SELLERS = ["flipkart.com", "amazon.in", "myntra.com", "croma.com", "reliancedigital.in", "vijaysales.com", "tatacliq.com", "ajio.com"]
LOCK = threading.Lock()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(d: datetime) -> str:
    return d.isoformat().replace("+00:00", "Z")


# ------------------------------------------------------------------ seed data

PRODUCTS = [
    ("Aurora 15 laptop", "aurora-15", 128990, "price_below", 120000),
    ("Nimbus wireless earbuds", "nimbus-buds", 5490, "price_below", 4500),
    ("Trail Runner shoes, size 9", "trail-runner", 6999, "price_below", 5500),
    ("Kestrel 27\" monitor", "kestrel-27", 21990, "price_below", 19000),
    ("Orbit espresso machine", "orbit-espresso", 38500, "price_below", 32000),
    ("Halcyon office chair", "halcyon-chair", 17450, "price_below", 15000),
    ("Atlas 1TB NVMe SSD", "atlas-ssd", 7299, "price_below", 6500),
    ("Lumen e-reader", "lumen-reader", 11999, "price_below", 9999),
    ("Vega mechanical keyboard", "vega-keys", 8790, "price_below", 7000),
    ("Pebble smartwatch", "pebble-watch", 14990, "price_below", 12000),
    ("Drift air purifier", "drift-purifier", 12450, "price_below", 11000),
    ("Summit 55L backpack", "summit-pack", 4290, "price_below", 3500),
]
TEXT_WATCHES = [
    ("Trail Runner back in stock", "trail-runner", "text_appears", "Back in stock"),
    ("Kestrel monitor pre-order", "kestrel-27", "text_appears", "Pre-order now"),
    ("Aurora 15 sold-out notice", "aurora-15", "text_disappears", "Currently unavailable"),
    ("Passport appointment slots", "notice", "text_disappears", "No slots available"),
]
PAGE_WATCHES = [("Shop returns policy", "returns"), ("Store notice board", "notice"), ("Warranty terms", "warranty")]
EVERY = [15, 30, 60, 60, 60, 120, 180, 360, 1440]


def build(empty: bool):
    rnd = random.Random(7)
    errands, checks, inbox, runs = [], [], [], []
    if empty:
        return errands, checks, inbox, runs
    t = now()
    n = 0
    variants = ["", " (blue)", " (gift)", " - 2nd", " v2"]
    for rep in range(4):
        for name, slug, price, kind, thr in PRODUCTS:
            n += 1
            nm = name + (variants[rep] if rep else "")
            errands.append(_errand(rnd, t, n, nm, f"{SHOP}/products/{slug}", kind, thr * (1 + rep * 0.02), price * (1 + rep * 0.03)))
    for name, slug, kind, kw in TEXT_WATCHES:
        n += 1
        e = _errand(rnd, t, n, name, f"{SHOP}/products/{slug}" if slug != "notice" else f"{SHOP}/notice", kind, None, None)
        e["spec"]["keyword"] = kw
        errands.append(e)
    for name, slug in PAGE_WATCHES:
        n += 1
        errands.append(_errand(rnd, t, n, name, f"{SHOP}/{slug}", "page_changed", None, None))
    n += 1
    b = _errand(rnd, t, n, "Best price: Aurora 15", None, "best_price_below", 125000, 124499)
    b["spec"]["query"] = "Aurora 15 RTX 5070 16GB 1TB"
    b["spec"]["url"] = None
    errands.append(b)
    # a few states
    errands[2]["active"] = False
    errands[9]["active"] = False
    errands[5]["consecutive_failures"] = 4
    errands[5]["failing"] = True
    errands[5]["last_status"] = "error"
    errands[17]["consecutive_failures"] = 3
    errands[17]["failing"] = True
    errands[17]["last_status"] = "error"
    errands[1]["condition_met"] = True
    errands[1]["last_status"] = "triggered"
    errands[1]["last_value"] = 4399
    errands[1]["previous_value"] = 4899
    errands[-1]["condition_met"] = True
    errands[-1]["last_status"] = "triggered"
    errands[-1]["previous_value"] = 129990
    errands[-2]["last_value"] = None
    errands[-2]["last_checked_at"] = None
    errands[-2]["last_status"] = None
    errands.sort(key=lambda e: e["created_at"], reverse=True)

    # global log
    cid = 0
    for e in errands:
        if not e["last_checked_at"]:
            continue
        every = e["spec"]["every_minutes"]
        val = e["last_value"]
        for k in range(rnd.randint(6, 22)):
            cid += 1
            at = t - timedelta(minutes=every * k + rnd.randint(0, 4), seconds=rnd.randint(0, 59))
            st = rnd.choices(["unchanged", "changed", "error", "triggered"], [70, 18, 8, 4])[0]
            if k == 0:
                st = e["last_status"] or "unchanged"
            if e["spec"]["kind"] == "page_changed" and k == 0:
                st = "baseline" if rnd.random() < 0.2 else st
            v = None
            if val is not None and st != "error":
                v = round(val * (1 + rnd.uniform(-0.04, 0.05) * (k > 0)), 0)
            checks.append({
                "id": cid, "errand_id": e["id"], "errand_name": e["name"], "at": iso(at),
                "duration_ms": rnd.randint(180, 2400) if st != "error" else rnd.randint(4000, 20000),
                "status": st, "value": v, "rendered": e["spec"]["render"] or rnd.random() < 0.1,
                "llm_used": rnd.random() < 0.08,
                "message": _msg(rnd, st, e, v),
            })
    checks.sort(key=lambda c: c["at"], reverse=True)

    for i in range(38):
        e = rnd.choice(errands)
        at = t - timedelta(hours=i * 7 + rnd.randint(0, 5), minutes=rnd.randint(0, 59))
        kind = e["spec"]["kind"]
        if kind in ("price_below", "best_price_below") and e["spec"].get("threshold"):
            p = int(e["spec"]["threshold"] * rnd.uniform(0.93, 0.995))
            title = f"{e['name']}: {_inr(p)} (target < {_inr(e['spec']['threshold'])})"
            body = f"The price dropped from {_inr(int(p * 1.06))}. Checked at {at.strftime('%H:%M')} UTC."
        elif kind in ("text_appears", "text_disappears"):
            title = f"{e['name']}: “{e['spec']['keyword']}” {'appeared' if kind == 'text_appears' else 'is gone'}"
            body = "Seen on the page during a scheduled check."
        else:
            title = f"{e['name']}: page changed"
            body = "About 4% of the visible text changed since the last check."
        inbox.append({"id": 1000 - i, "errand_id": e["id"], "errand_name": e["name"], "at": iso(at), "title": title,
                      "body": body, "read": i >= 6, "delivered_to": "webhook" if i % 3 == 0 else None})

    for i, (q, best, cnt) in enumerate([
        ("Aurora 15 RTX 5070 16GB 1TB", 124499, 5), ("Nimbus wireless earbuds", 4399, 4), ("Kestrel 27 inch 4K monitor", 20990, 6),
        ("Atlas 1TB NVMe SSD", 6899, 7), ("Orbit espresso machine", None, 3), ("Lumen e-reader 7 inch", 10999, 5),
        ("Vega mechanical keyboard TKL", 7490, 6), ("Halcyon ergonomic chair", 15990, 4), ("Pebble smartwatch 2", 13490, 5),
        ("Summit 55L backpack", 3890, 4), ("Drift air purifier HEPA", 11250, 6), ("Trail Runner shoes size 9", 5999, 5),
        ("Aurora 15 laptop", 124499, 5), ("Nimbus buds", 4490, 3),
    ]):
        created = t - timedelta(hours=i * 9 + 2, minutes=i * 7)
        if q.lower().startswith("aurora"):
            offers = aurora_offers()
        else:
            offers = _fake_offers(rnd, q, best, cnt)
        ok = [o for o in offers if o.get("price") is not None and not o.get("error")]
        bo = min(ok, key=lambda o: o["price"]) if ok else None
        runs.append({"id": str(uuid.UUID(int=rnd.getrandbits(128))), "query": q, "status": "done" if best or ok else "done",
                     "product": {"title": q if not q.lower().startswith("aurora") else "Aurora 15 (RTX 5070, 16 GB, 1 TB)"},
                     "offers": offers, "best": bo, "notes": ["Searched 8 sellers; 1 blocked automated access."] if ok else ["No seller listed a price for this product."],
                     "created_at": iso(created), "finished_at": iso(created + timedelta(seconds=rnd.randint(9, 26))),
                     "errand_id": None, "_t0": None})
    runs[0]["errand_id"] = errands[0]["id"] if errands else None
    return errands, checks, inbox, runs


def _inr(v) -> str:
    v = int(round(v))
    s = str(v)
    if len(s) <= 3:
        return "₹" + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts) + "," + tail


def _msg(rnd, st, e, v):
    if st == "error":
        return rnd.choice(["HTTP 503 from upstream", "Timed out after 20 s", "Price not found on the page", "HTTP 403 (bot protection)"])
    if st == "triggered":
        return "Condition met: " + (f"{_inr(v)} is below target" if v else "text matched")
    if st == "changed":
        return f"Price changed to {_inr(v)}" if v else "Content changed (6% of lines)"
    if st == "baseline":
        return "Baseline captured"
    return f"Price {_inr(v)} via json-ld" if v else "No change"


def _errand(rnd, t, n, name, url, kind, thr, val):
    every = EVERY[n % len(EVERY)]
    created = t - timedelta(days=n * 1.7 + rnd.random(), hours=rnd.randint(0, 20))
    last = t - timedelta(minutes=rnd.randint(1, min(every, 90)))
    spec = {"name": name, "url": url, "kind": kind, "threshold": round(thr, -1) if thr else None, "keyword": None,
            "selector": ".price-now" if n % 5 == 0 else None, "render": n % 7 == 0, "every_minutes": every}
    lv = round(val, 0) if val else None
    pv = round(lv * rnd.choice([1, 1, 1.03, 0.97, 1.08, 1]), 0) if lv else None
    return {
        "id": str(uuid.UUID(int=rnd.getrandbits(128))), "name": name, "prompt": f"Tell me when {name.lower()} ... check every {every} minutes",
        "spec": spec, "active": True, "created_at": iso(created), "last_checked_at": iso(last),
        "last_status": "unchanged", "last_value": lv, "previous_value": pv, "condition_met": False,
        "consecutive_failures": 0, "selector_learned": "span[itemprop=price]" if kind in ("price_below", "price_above") and n % 3 else None,
        "schedule": _cron(every), "condition": "", "failing": False,
    }


def _cron(n):
    if n < 60:
        return f"*/{n} * * * *"
    if n == 60:
        return "0 * * * *"
    if n < 1440:
        return f"0 */{n // 60} * * *"
    return "0 8 * * *"


def aurora_offers():
    return [
        {"seller": "PixelMart", "url": f"{SHOP}/sellers/pixelmart/aurora-15", "title": "Aurora 15 Laptop (RTX 5070, 16 GB, 1 TB SSD)",
         "price": 129990, "currency": "INR", "mrp": 149990, "in_stock": True, "source": "jsonld", "match_score": 0.96,
         "match_reason": "Same model: Aurora 15, RTX 5070, 16 GB, 1 TB all agree."},
        {"seller": "Bazaar99", "url": f"{SHOP}/sellers/bazaar99/aurora-15", "title": "Aurora 15 | RTX 5070 | 16GB | 1TB",
         "price": 124499, "currency": "INR", "mrp": 139990, "in_stock": True, "source": "meta", "match_score": 0.91,
         "match_reason": "Title tokens 91% similar; RTX 5070 and 16GB match."},
        {"seller": "Northwind", "url": f"{SHOP}/sellers/northwind/aurora-15", "title": "Aurora 15 gaming laptop",
         "price": 131000, "currency": "INR", "mrp": None, "in_stock": None, "source": "heuristic", "match_score": 0.68,
         "match": "possible", "match_reason": "Name matches but the listing does not state GPU or memory. Kept as a possible match."},
        {"seller": "ByteBay", "url": f"{SHOP}/sellers/bytebay/aurora-15-pro", "title": "Aurora 15 Pro RTX 5080",
         "price": None, "currency": "INR", "in_stock": True, "source": "jsonld", "match_score": 0.41,
         "error": "Different product: RTX 5080 does not match RTX 5070"},
        {"seller": "QuietCart", "url": f"{SHOP}/sellers/quietcart/aurora-15", "title": None, "price": None,
         "error": "Blocked by robots.txt"},
    ]


def _fake_offers(rnd, q, best, cnt):
    out = []
    sellers = rnd.sample(SELLERS, cnt)
    for i, s in enumerate(sellers):
        if best is None or i == cnt - 1 and rnd.random() < 0.7:
            out.append({"seller": s, "url": f"https://www.{s}/p/{i}", "title": q, "price": None,
                        "error": rnd.choice(["HTTP 403 / bot protection", "No price found", "Timeout"])})
            continue
        p = best if i == 0 else int(best * rnd.uniform(1.01, 1.22))
        out.append({"seller": s, "url": f"https://www.{s}/p/{i}", "title": q.title(), "price": p, "currency": "INR",
                    "mrp": int(p * rnd.uniform(1.1, 1.4)) if rnd.random() < 0.7 else None,
                    "in_stock": rnd.random() > 0.15, "source": rnd.choice(["jsonld", "meta", "heuristic"]),
                    "match_score": round(rnd.uniform(0.78, 0.99), 2), "match_reason": "Model name and capacity agree."})
    return out


# ------------------------------------------------------------------ helpers

def paginate(items, qs, default=25):
    page = max(1, int(qs.get("page", ["1"])[0] or 1))
    per = min(100, max(1, int(qs.get("per_page", [str(default)])[0] or default)))
    total = len(items)
    pages = max(1, -(-total // per))
    s = (page - 1) * per
    return {"items": items[s:s + per], "total": total, "page": page, "per_page": per, "pages": pages}


def run_view(r):
    """Advance a live run by wall-clock time: queued -> searching -> fetching -> done."""
    if r.get("_t0") is None:
        return {k: v for k, v in r.items() if not k.startswith("_")}
    el = time.time() - r["_t0"]
    out = {k: v for k, v in r.items() if not k.startswith("_")}
    full = r["_offers"]
    if el < 1.2:
        out.update(status="queued", offers=[], best=None)
    elif el < 3.5:
        out.update(status="searching", offers=[], best=None)
    elif el < 3.5 + 0.8 * len(full):
        k = int((el - 3.5) / 0.8) + 1
        out.update(status="fetching", offers=full[:k], best=None)
    else:
        ok = [o for o in full if o.get("price") is not None and not o.get("error")]
        best = min((o for o in ok if o.get("match") != "possible"), key=lambda o: o["price"], default=None)
        out.update(status="done", offers=full, best=best,
                   finished_at=iso(datetime.fromtimestamp(r["_t0"] + 3.5 + 0.8 * len(full), timezone.utc)))
    return out


class Handler(SimpleHTTPRequestHandler):
    server_version = "VigilMock/1"
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript", ".css": "text/css",
                      ".svg": "image/svg+xml", ".html": "text/html"}

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):  # quiet
        pass

    def end_headers(self):
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; "
                         "font-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, code, data=None):
        body = b"" if data is None else json.dumps(data).encode()
        self.send_response(code)
        if data is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path.startswith("/api/"):
            return self.api("GET", u.path, parse_qs(u.query))
        if u.path == "/healthz":
            return self._json(200, {"ok": True})
        return super().do_GET()

    def do_POST(self):
        u = urlsplit(self.path)
        return self.api("POST", u.path, parse_qs(u.query))

    def do_PATCH(self):
        u = urlsplit(self.path)
        return self.api("PATCH", u.path, parse_qs(u.query))

    def do_DELETE(self):
        u = urlsplit(self.path)
        return self.api("DELETE", u.path, parse_qs(u.query))

    # -------------------------------------------------------------- API
    def api(self, method, path, qs):
        S = self.server.state
        time.sleep(0.12)  # a little latency so loading states are real
        with LOCK:
            errands, checks, inbox, runs = S["errands"], S["checks"], S["inbox"], S["runs"]
            by_id = {e["id"]: e for e in errands}

            if path == "/api/system" and method == "GET":
                return self._json(200, {"version": "0.5.0-dev", "env": "dev", "llm_mode": "demo", "llm_budget_used": 37,
                                        "llm_daily_budget": 200, "queue_depth": 3, "scheduler": "kubernetes",
                                        "min_interval_minutes": 1, "search_provider": "demo", "compare_sellers": SELLERS})

            if path == "/api/errands" and method == "GET":
                q = (qs.get("q", [""])[0] or "").lower()
                status = qs.get("status", [""])[0]
                items = [e for e in errands if not q or q in e["name"].lower() or q in (e["spec"].get("url") or "").lower()]
                if status == "active":
                    items = [e for e in items if e["active"] and not e["failing"]]
                elif status == "paused":
                    items = [e for e in items if not e["active"]]
                elif status == "failing":
                    items = [e for e in items if e["failing"]]
                return self._json(200, paginate(items, qs))

            if path == "/api/errands" and method == "POST":
                b = self._body()
                spec = b.get("spec") or {}
                if not spec.get("name"):
                    return self._json(422, {"detail": [{"loc": ["body", "spec", "name"], "msg": "Field required"}]})
                e = _errand(random.Random(), now(), 1, spec["name"], spec.get("url"), spec.get("kind"), spec.get("threshold"), None)
                e["spec"] = spec
                e["prompt"] = b.get("prompt")
                e["last_checked_at"] = None
                e["last_status"] = None
                e["last_value"] = None
                e["previous_value"] = None
                e["created_at"] = iso(now())
                e["schedule"] = _cron(spec.get("every_minutes", 60))
                errands.insert(0, e)
                return self._json(201, e)

            m = re.fullmatch(r"/api/errands/([^/]+)(/history|/checks|/check)?", path)
            if m:
                e = by_id.get(m.group(1))
                if e is None:
                    return self._json(404, {"detail": "errand not found"})
                sub = m.group(2)
                if sub is None and method == "GET":
                    return self._json(200, e)
                if sub is None and method == "PATCH":
                    e["active"] = bool(self._body().get("active"))
                    return self._json(200, e)
                if sub is None and method == "DELETE":
                    errands.remove(e)
                    S["checks"] = [c for c in checks if c["errand_id"] != e["id"]]
                    return self._json(204)
                if sub == "/check" and method == "POST":
                    return self._json(202, {"queued": True})
                if sub == "/checks":
                    return self._json(200, paginate([c for c in checks if c["errand_id"] == e["id"]], qs, 50))
                if sub == "/history":
                    days = int(qs.get("days", ["30"])[0])
                    return self._json(200, history(e, days))

            if path == "/api/log" and method == "GET":
                items = checks
                if qs.get("status", [""])[0]:
                    items = [c for c in items if c["status"] == qs["status"][0]]
                if qs.get("errand_id", [""])[0]:
                    items = [c for c in items if c["errand_id"] == qs["errand_id"][0]]
                return self._json(200, paginate(items, qs, 100))

            if path == "/api/inbox" and method == "GET":
                items = inbox
                un = qs.get("unread", [""])[0]
                if un == "true":
                    items = [n for n in items if not n["read"]]
                elif un == "false":
                    items = [n for n in items if n["read"]]
                return self._json(200, paginate(items, qs, 50))
            if path == "/api/inbox/unread-count":
                return self._json(200, {"count": sum(1 for n in inbox if not n["read"])})
            if path == "/api/inbox/read-all" and method == "POST":
                c = 0
                for n in inbox:
                    c += not n["read"]
                    n["read"] = True
                return self._json(200, {"ok": True, "updated": c})
            m = re.fullmatch(r"/api/inbox/(\d+)/read", path)
            if m and method == "POST":
                for n in inbox:
                    if n["id"] == int(m.group(1)):
                        n["read"] = True
                        return self._json(200, {"ok": True})
                return self._json(404, {"detail": "notification not found"})

            if path == "/api/plan" and method == "POST":
                return self._json(200, plan(self._body().get("text", "")))
            if path == "/api/dryrun" and method == "POST":
                spec = self._body().get("spec") or {}
                LOCK.release()
                try:
                    time.sleep(2.5)
                finally:
                    LOCK.acquire()
                if "fail" in (spec.get("url") or ""):
                    return self._json(200, {"ok": False, "value": None, "condition_true": None, "rendered": False, "selector": None,
                                            "excerpt": "", "message": "HTTP 404 from vigil-demo-shop"})
                v = 118990 if spec.get("kind") in ("price_below", "price_above", "best_price_below") else None
                cond = (v is not None and spec.get("threshold") and v < spec["threshold"]) if v else (spec.get("kind") == "text_appears")
                return self._json(200, {"ok": True, "value": v, "condition_true": bool(cond), "rendered": bool(spec.get("render")),
                                        "selector": "span[itemprop=price]" if v else None,
                                        "excerpt": "Aurora 15 Laptop — ₹1,18,990 incl. taxes. In stock. Free delivery by Friday.",
                                        "message": "Found ₹1,18,990 via json-ld."})

            if path == "/api/compare" and method == "POST":
                q = (self._body().get("query") or "").strip()
                if len(q) < 2:
                    return self._json(422, {"detail": "query is too short"})
                rid = str(uuid.uuid4())
                offers = aurora_offers() if "aurora" in q.lower() else []
                title = "Aurora 15 (RTX 5070, 16 GB, 1 TB)" if "aurora" in q.lower() else q
                notes = ["Searched 8 sellers with the demo search provider.", "QuietCart disallows automated access in robots.txt; skipped."] if offers else \
                    ["The demo search provider only knows the fictional “Aurora 15” laptop."]
                runs.insert(0, {"id": rid, "query": q, "status": "queued", "product": {"title": title}, "offers": [], "best": None,
                                "notes": notes, "created_at": iso(now()), "finished_at": None, "errand_id": None,
                                "_t0": time.time(), "_offers": offers})
                return self._json(202, {"id": rid, "status": "queued"})
            if path == "/api/compare" and method == "GET":
                return self._json(200, paginate([run_view(r) for r in runs], qs, 10))
            m = re.fullmatch(r"/api/compare/([^/]+)(/track)?", path)
            if m:
                r = next((x for x in runs if x["id"] == m.group(1)), None)
                if r is None:
                    return self._json(404, {"detail": "comparison not found"})
                if m.group(2) and method == "POST":
                    b = self._body()
                    v = run_view(r)
                    spec = {"name": "Best price: " + (v["product"] or {}).get("title", r["query"])[:60], "kind": "best_price_below",
                            "url": None, "query": r["query"], "threshold": b.get("threshold"), "keyword": None, "selector": None,
                            "render": False, "every_minutes": b.get("every_minutes", 120)}
                    e = _errand(random.Random(), now(), 1, spec["name"], None, "best_price_below", None, None)
                    e.update(spec=spec, last_value=(v["best"] or {}).get("price"), previous_value=None, created_at=iso(now()),
                             schedule=_cron(spec["every_minutes"]))
                    errands.insert(0, e)
                    r["errand_id"] = e["id"]
                    return self._json(201, e)
                return self._json(200, run_view(r))

        return self._json(404, {"detail": "Not Found"})


def history(e, days):
    rnd = random.Random(e["id"] + str(days))
    t = now()
    kind = e["spec"]["kind"]
    every_h = max(1.0, e["spec"]["every_minutes"] / 60)
    n = int(min(180, days * 24 / every_h))
    pts = []
    base = e["last_value"] or 0
    v = base * 1.12 if base else None
    for i in range(n, -1, -1):
        at = t - timedelta(hours=i * every_h * (days * 24 / every_h / max(n, 1)))
        st = "unchanged"
        if v is not None:
            if rnd.random() < 0.08:
                v = round(v * rnd.uniform(0.95, 1.04), -1)
                st = "changed"
            if i == 0:
                v = base
        if rnd.random() < 0.04:
            pts.append({"at": iso(at), "value": None, "status": "error"})
            continue
        if kind in ("text_appears", "text_disappears", "page_changed"):
            st = rnd.choices(["unchanged", "changed", "triggered"], [85, 12, 3])[0]
        pts.append({"at": iso(at), "value": v, "status": st})
    return pts


def plan(text):
    url = re.search(r"https?://\S+", text)
    nums = [int(x.replace(",", "")) for x in re.findall(r"₹\s?([\d,]{3,})", text)]
    every = 60
    m = re.search(r"every\s+(\d+)\s*(min|minute|hour|h)", text, re.I)
    if m:
        every = int(m.group(1)) * (60 if m.group(2).lower().startswith("h") else 1)
    elif re.search(r"daily", text, re.I):
        every = 1440
    low = text.lower()
    kw = re.search(r"[“\"]([^”\"]+)[”\"]", text)
    if "best price" in low or ("across" in low and "seller" in low):
        kind = "best_price_below"
    elif kw and ("appear" in low or "in stock" in low):
        kind = "text_appears"
    elif nums:
        kind = "price_above" if "above" in low else "price_below"
    elif "change" in low:
        kind = "page_changed"
    else:
        kind = "page_changed"
    name = "Aurora 15 laptop" if "aurora" in low else (kw.group(1) if kw else "New watch")
    spec = {"name": name, "url": url.group(0).rstrip(".,") if url else None, "kind": kind, "threshold": nums[0] if nums else None,
            "keyword": kw.group(1) if kw else None, "selector": None, "render": False, "every_minutes": every}
    if kind == "best_price_below":
        spec["query"] = "Aurora 15" if "aurora" in low else name
        spec["name"] = "Best price: " + spec["query"]
    notes = ["Interval snapped to an allowed value." if m else "No interval given; defaulting to hourly."]
    if kind == "best_price_below":
        notes.append("Best-price watches search every seller; 60 minutes or slower is recommended for real sites.")
    return {"spec": spec, "source": "rules", "notes": notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8099)
    ap.add_argument("--empty", action="store_true")
    a = ap.parse_args()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    e, c, i, r = build(a.empty)
    srv.state = {"errands": e, "checks": c, "inbox": i, "runs": r}
    print(f"Vigil mock UI on http://127.0.0.1:{a.port}/  ({len(e)} watches, {len(c)} checks)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
