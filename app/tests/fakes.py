"""In-memory fakes for the store, fetcher, LLM and scheduler (no DB / network needed)."""

from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime

from vigil.fetch import FetchError, FetchResult


class MemoryStore:
    def __init__(self):
        self.errands: dict[str, dict] = {}
        self.checks: list[dict] = []
        self.notifications: list[dict] = []
        self._ids = itertools.count(1)

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
