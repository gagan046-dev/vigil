"""In-memory fakes for the store, fetcher, LLM and scheduler (no DB / network needed)."""

from __future__ import annotations

import copy
import itertools
import uuid
from datetime import UTC, datetime, timedelta

from vigil.fetch import FetchError, FetchResult


class MemoryStore:
    def __init__(self):
        self.errands: dict[str, dict] = {}
        self.checks: list[dict] = []
        self.notifications: list[dict] = []
        self.compares: dict[str, dict] = {}
        self._ids = itertools.count(1)
        self._clock = itertools.count(1)

    def ping(self):
        return True

    def schema_present(self):
        return True

    def create_errand(self, name, spec, prompt):
        eid = str(uuid.uuid4())
        row = {
            "id": eid,
            "name": name,
            "prompt": prompt,
            "spec": dict(spec),
            "active": True,
            "created_at": datetime.now(UTC),
            "last_checked_at": None,
            "last_status": None,
            "last_value": None,
            "last_hash": None,
            "last_text": None,
            "condition_met": False,
            "consecutive_failures": 0,
            "selector_learned": None,
        }
        self.errands[eid] = row
        return dict(row)

    def get_errand(self, eid):
        row = self.errands.get(eid)
        return dict(row) if row else None

    def list_errands(self):
        return [dict(e) for e in sorted(self.errands.values(), key=lambda e: e["created_at"], reverse=True)]

    def set_active(self, eid, active):
        if eid not in self.errands:
            return None
        self.errands[eid]["active"] = active
        return dict(self.errands[eid])

    def delete_errand(self, eid):
        return self.errands.pop(eid, None) is not None

    def update_errand_state(self, eid, **fields):
        if eid in self.errands:
            if fields.get("last_text") is not None:
                fields["last_text"] = fields["last_text"][:20000]
            self.errands[eid].update(fields)

    def add_check(self, errand_id, status, duration_ms=None, value=None, rendered=False, llm_used=False, message=None):
        self.checks.append(
            {
                "id": next(self._ids),
                "errand_id": errand_id,
                "at": datetime.now(UTC),
                "status": status,
                "duration_ms": duration_ms,
                "value": value,
                "rendered": rendered,
                "llm_used": llm_used,
                "message": message,
            }
        )

    def list_checks(self, eid, limit=50):
        return [c for c in reversed(self.checks) if c["errand_id"] == eid][:limit]

    def recent_log(self, limit=100):
        out = []
        for c in reversed(self.checks):
            e = self.errands.get(c["errand_id"])
            if e:
                out.append({**c, "errand_name": e["name"]})
        return out[:limit]

    def add_notification(self, errand_id, title, body, delivered_to):
        row = {
            "id": next(self._ids),
            "errand_id": errand_id if errand_id in self.errands else None,
            "at": datetime.now(UTC),
            "title": title,
            "body": body,
            "read": False,
            "delivered_to": delivered_to,
        }
        self.notifications.append(row)
        return dict(row)

    def list_inbox(self, limit=50):
        return list(reversed(self.notifications))[:limit]

    def mark_read(self, nid):
        for n in self.notifications:
            if n["id"] == nid:
                n["read"] = True
                return True
        return False

    # -- pagination (same contract as PgStore: (items, total))
    @staticmethod
    def _slice(rows, offset, limit):
        return [dict(r) for r in rows[offset : offset + limit]], len(rows)

    def _status(self, e):
        if not e.get("active", True):
            return "paused"
        return "failing" if int(e.get("consecutive_failures") or 0) >= 3 else "active"

    def _prev_value(self, e):
        for c in reversed(self.checks):
            if c["errand_id"] == e["id"] and c["value"] is not None and c["value"] != e.get("last_value"):
                return c["value"]
        return None

    def page_errands(self, offset=0, limit=25, q=None, status=None):
        rows = self.list_errands()
        if q:
            ql = q.lower()
            rows = [
                e
                for e in rows
                if ql in e["name"].lower()
                or ql in str(e["spec"].get("url") or "").lower()
                or ql in str(e["spec"].get("query") or "").lower()
            ]
        if status:
            rows = [e for e in rows if self._status(e) == status]
        rows = [{**e, "prev_value": self._prev_value(e)} for e in rows]
        return self._slice(rows, offset, limit)

    def page_checks(self, eid, offset=0, limit=25):
        return self._slice([c for c in reversed(self.checks) if c["errand_id"] == eid], offset, limit)

    def errand_history(self, eid, days=30):
        since = datetime.now(UTC) - timedelta(days=days)
        rows = [c for c in self.checks if c["errand_id"] == eid and c["at"] >= since]
        return [{"at": c["at"], "value": c["value"], "status": c["status"]} for c in rows]

    def page_log(self, offset=0, limit=25, status=None, errand_id=None):
        rows = [
            r
            for r in self.recent_log(10**9)
            if (status is None or r["status"] == status) and (errand_id is None or r["errand_id"] == errand_id)
        ]
        return self._slice(rows, offset, limit)

    def page_inbox(self, offset=0, limit=25, unread=None):
        rows = []
        for n in reversed(self.notifications):
            if unread is not None and n["read"] == unread:
                continue
            e = self.errands.get(n["errand_id"]) if n["errand_id"] else None
            rows.append({**n, "errand_name": e["name"] if e else None})
        return self._slice(rows, offset, limit)

    def unread_count(self):
        return sum(1 for n in self.notifications if not n["read"])

    def mark_all_read(self):
        n = 0
        for row in self.notifications:
            if not row["read"]:
                row["read"] = True
                n += 1
        return n

    # -- price comparisons
    def create_compare(self, query, errand_id=None, notes=None):
        cid = str(uuid.uuid4())
        row = {
            "id": cid,
            "query": query,
            "status": "queued",
            "product": None,
            "offers": [],
            "best": None,
            "notes": dict(notes or {}),
            "created_at": datetime.now(UTC) + timedelta(microseconds=next(self._clock)),
            "finished_at": None,
            "errand_id": errand_id,
        }
        self.compares[cid] = row
        return copy.deepcopy(row)

    def get_compare(self, cid):
        row = self.compares.get(cid)
        return copy.deepcopy(row) if row else None

    def update_compare(self, cid, **fields):
        if cid in self.compares:
            allowed = ("status", "product", "offers", "best", "notes", "finished_at")
            self.compares[cid].update(copy.deepcopy({k: v for k, v in fields.items() if k in allowed}))

    def page_compares(self, offset=0, limit=25, errand_id=None):
        rows = sorted(self.compares.values(), key=lambda r: r["created_at"], reverse=True)
        rows = [r for r in rows if r["errand_id"] == errand_id]
        rows = [{**{k: v for k, v in r.items() if k != "offers"}, "offers_count": len(r["offers"])} for r in rows]
        return self._slice(rows, offset, limit)

    def latest_compare(self, eid):
        rows = [r for r in self.compares.values() if r["errand_id"] == eid]
        return copy.deepcopy(max(rows, key=lambda r: r["created_at"])) if rows else None

    def prune_compares(self, eid, keep=50):
        rows = sorted(
            (r for r in self.compares.values() if r["errand_id"] == eid), key=lambda r: r["created_at"], reverse=True
        )
        for r in rows[keep:]:
            del self.compares[r["id"]]


class FakeFetcher:
    """pages[(url, render)] -> html (or an Exception to raise). Records calls."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.calls: list[tuple[str, bool]] = []

    def set(self, url, html, render=False):
        self.pages[(url, render)] = html

    def fetch(self, url, render=False):
        self.calls.append((url, render))
        page = self.pages.get((url, render))
        if page is None and render:
            page = self.pages.get((url, False))
        if page is None:
            raise FetchError("HTTP 404 from test")
        if isinstance(page, Exception):
            raise page
        return FetchResult(page, 200, url, rendered=render, duration_ms=5)


class FakeLLM:
    def __init__(self, replies=None, available=True):
        self.replies = list(replies or [])
        self._available = available
        self.calls: list[tuple[str, str]] = []

    mode = "gateway"

    def available(self):
        return self._available

    def chat_json(self, system, user, purpose="other"):
        self.calls.append((purpose, user))
        return self.replies.pop(0) if self.replies else None


class FakeScheduler:
    name = "fake"

    def __init__(self):
        self.upserts: list[dict] = []
        self.deleted: list[str] = []

    def upsert(self, errand):
        self.upserts.append(errand)

    def delete(self, eid):
        self.deleted.append(eid)

    def reconcile(self, errands):
        return {}

    def start(self):
        pass

    def stop(self):
        pass
