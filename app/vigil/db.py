"""Postgres storage (sync psycopg3 + connection pool).

PgStore is the only class that talks SQL; the API, worker and notifier depend on
its method names, which tests replace with an in-memory fake.
"""

from __future__ import annotations

import json
from typing import Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .migrate import TABLES

LAST_TEXT_LIMIT = 20_000

ERRAND_FIELDS = (
    "last_checked_at",
    "last_status",
    "last_value",
    "last_hash",
    "last_text",
    "condition_met",
    "consecutive_failures",
    "selector_learned",
)

ERRAND_STATUS_SQL = {
    "active": "e.active AND e.consecutive_failures < 3",
    "paused": "NOT e.active",
    "failing": "e.active AND e.consecutive_failures >= 3",
}
COMPARE_FIELDS = ("status", "product", "offers", "best", "notes", "finished_at")
COMPARE_JSON = ("product", "offers", "best", "notes")


def _norm(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    out = dict(row)
    for key in ("id", "errand_id"):
        if out.get(key) is not None:
            out[key] = str(out[key])
    if isinstance(out.get("spec"), str):
        out["spec"] = json.loads(out["spec"])
    return out


class PgStore:
    def __init__(self, conninfo: str, min_size: int = 1, max_size: int = 5):
        self.pool = ConnectionPool(
            conninfo,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"autocommit": True, "row_factory": dict_row},
        )
        self.pool.open(wait=False)

    def close(self) -> None:
        self.pool.close()

    def _all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self.pool.connection(timeout=10) as conn:
            return [_norm(r) for r in conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self.pool.connection(timeout=10) as conn:
            return _norm(conn.execute(sql, params).fetchone())

    # -- health
    def ping(self) -> bool:
        return self._one("SELECT 1 AS ok") is not None

    def schema_present(self) -> bool:
        row = self._one(
            "SELECT count(*) AS n FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ANY(%s)",
            (list(TABLES),),
        )
        return bool(row) and row["n"] == len(TABLES)

    # -- errands
    def create_errand(self, name: str, spec: dict, prompt: str | None) -> dict:
        return self._one(
            "INSERT INTO errands (name, prompt, spec) VALUES (%s, %s, %s) RETURNING *", (name, prompt, Jsonb(spec))
        )

    def get_errand(self, errand_id: str) -> dict | None:
        return self._one("SELECT * FROM errands WHERE id = %s", (errand_id,))

    def list_errands(self) -> list[dict]:
        return self._all("SELECT * FROM errands ORDER BY created_at DESC")

    def set_active(self, errand_id: str, active: bool) -> dict | None:
        return self._one("UPDATE errands SET active = %s WHERE id = %s RETURNING *", (active, errand_id))

    def delete_errand(self, errand_id: str) -> bool:
        return self._one("DELETE FROM errands WHERE id = %s RETURNING id", (errand_id,)) is not None

    def update_errand_state(self, errand_id: str, **fields: Any) -> None:
        cols = [k for k in fields if k in ERRAND_FIELDS]
        if not cols:
            return
        if fields.get("last_text") is not None:
            fields["last_text"] = fields["last_text"][:LAST_TEXT_LIMIT]
        sets = ", ".join(f"{c} = %({c})s" for c in cols)
        params = {c: fields[c] for c in cols}
        params["id"] = errand_id
        with self.pool.connection(timeout=10) as conn:
            conn.execute(f"UPDATE errands SET {sets} WHERE id = %(id)s", params)  # noqa: S608 (cols allow-listed)

    # -- checks
    def add_check(
        self,
        errand_id: str,
        status: str,
        duration_ms: int | None = None,
        value: float | None = None,
        rendered: bool = False,
        llm_used: bool = False,
        message: str | None = None,
    ) -> None:
        with self.pool.connection(timeout=10) as conn:
            conn.execute(
                "INSERT INTO checks (errand_id, status, duration_ms, value, rendered, llm_used, message) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (errand_id, status, duration_ms, value, rendered, llm_used, (message or "")[:1000]),
            )

    def list_checks(self, errand_id: str, limit: int = 50) -> list[dict]:
        return self._all("SELECT * FROM checks WHERE errand_id = %s ORDER BY at DESC LIMIT %s", (errand_id, limit))

    def recent_log(self, limit: int = 100) -> list[dict]:
        return self._all(
            "SELECT c.*, e.name AS errand_name FROM checks c JOIN errands e ON e.id = c.errand_id "
            "ORDER BY c.at DESC LIMIT %s",
            (limit,),
        )

    # -- notifications
    def add_notification(self, errand_id: str | None, title: str, body: str, delivered_to: str) -> dict:
        return self._one(
            "INSERT INTO notifications (errand_id, title, body, delivered_to) "
            "VALUES ((SELECT id FROM errands WHERE id = %s), %s, %s, %s) RETURNING *",
            (errand_id, title[:300], body[:4000], delivered_to),
        )

    def list_inbox(self, limit: int = 50) -> list[dict]:
        return self._all(
            "SELECT n.*, e.name AS errand_name FROM notifications n "
            "LEFT JOIN errands e ON e.id = n.errand_id ORDER BY n.at DESC LIMIT %s",
            (limit,),
        )

    def mark_read(self, notification_id: int) -> bool:
        return (
            self._one("UPDATE notifications SET read = true WHERE id = %s RETURNING id", (notification_id,)) is not None
        )

    # -- pagination (each returns (items, total))
    def _page(self, select: str, count: str, where: list[str], params: dict, order: str, offset: int, limit: int):
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self._one(count + clause, params)["n"]
        rows = self._all(
            f"{select}{clause} ORDER BY {order} LIMIT %(limit)s OFFSET %(offset)s",
            {
                **params,
                "limit": limit,
                "offset": offset,
            },
        )
        return rows, int(total)

    def page_errands(self, offset: int = 0, limit: int = 25, q: str | None = None, status: str | None = None):
        where: list[str] = []
        params: dict = {}
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            params["q"] = f"%{escaped}%"
            where.append("(e.name ILIKE %(q)s OR e.spec->>'url' ILIKE %(q)s OR e.spec->>'query' ILIKE %(q)s)")
        if status in ERRAND_STATUS_SQL:
            where.append(ERRAND_STATUS_SQL[status])
        return self._page(
            "SELECT e.*, (SELECT c.value FROM checks c WHERE c.errand_id = e.id AND c.value IS NOT NULL "
            "AND c.value IS DISTINCT FROM e.last_value ORDER BY c.at DESC LIMIT 1) AS prev_value FROM errands e",
            "SELECT count(*) AS n FROM errands e",
            where,
            params,
            "e.created_at DESC, e.id",
            offset,
            limit,
        )

    def page_checks(self, errand_id: str, offset: int = 0, limit: int = 25):
        return self._page(
            "SELECT * FROM checks c",
            "SELECT count(*) AS n FROM checks c",
            ["c.errand_id = %(eid)s"],
            {"eid": errand_id},
            "c.at DESC, c.id DESC",
            offset,
            limit,
        )

    def errand_history(self, errand_id: str, days: int = 30) -> list[dict]:
        return self._all(
            "SELECT at, value, status FROM checks WHERE errand_id = %s AND at >= now() - make_interval(days => %s) "
            "ORDER BY at ASC LIMIT 5000",
            (errand_id, days),
        )

    def page_log(self, offset: int = 0, limit: int = 25, status: str | None = None, errand_id: str | None = None):
        where: list[str] = []
        params: dict = {}
        if status:
            where.append("c.status = %(status)s")
            params["status"] = status
        if errand_id:
            where.append("c.errand_id = %(eid)s")
            params["eid"] = errand_id
        return self._page(
            "SELECT c.*, e.name AS errand_name FROM checks c JOIN errands e ON e.id = c.errand_id",
            "SELECT count(*) AS n FROM checks c",
            where,
            params,
            "c.at DESC, c.id DESC",
            offset,
            limit,
        )

    def page_inbox(self, offset: int = 0, limit: int = 25, unread: bool | None = None):
        where = [] if unread is None else (["NOT n.read"] if unread else ["n.read"])
        return self._page(
            "SELECT n.*, e.name AS errand_name FROM notifications n LEFT JOIN errands e ON e.id = n.errand_id",
            "SELECT count(*) AS n FROM notifications n",
            where,
            {},
            "n.at DESC, n.id DESC",
            offset,
            limit,
        )

    def unread_count(self) -> int:
        return int(self._one("SELECT count(*) AS n FROM notifications WHERE NOT read")["n"])

    def mark_all_read(self) -> int:
        with self.pool.connection(timeout=10) as conn:
            return conn.execute("UPDATE notifications SET read = true WHERE NOT read").rowcount

    # -- price comparisons
    def create_compare(self, query: str, errand_id: str | None = None, notes: dict | None = None) -> dict:
        return self._one(
            "INSERT INTO compare_runs (query, errand_id, notes) VALUES (%s, %s, %s) RETURNING *",
            (query[:500], errand_id, Jsonb(notes or {})),
        )

    def get_compare(self, compare_id: str) -> dict | None:
        return self._one("SELECT * FROM compare_runs WHERE id = %s", (compare_id,))

    def update_compare(self, compare_id: str, **fields: Any) -> None:
        cols = [k for k in fields if k in COMPARE_FIELDS]
        if not cols:
            return
        params = {c: (Jsonb(fields[c]) if c in COMPARE_JSON and fields[c] is not None else fields[c]) for c in cols}
        params["id"] = compare_id
        sets = ", ".join(f"{c} = %({c})s" for c in cols)
        with self.pool.connection(timeout=10) as conn:
            conn.execute(f"UPDATE compare_runs SET {sets} WHERE id = %(id)s", params)  # noqa: S608 (allow-listed)

    def page_compares(self, offset: int = 0, limit: int = 25, errand_id: str | None = None):
        where, params = (["r.errand_id = %(eid)s"], {"eid": errand_id}) if errand_id else (["r.errand_id IS NULL"], {})
        return self._page(
            "SELECT r.id, r.query, r.status, r.product, r.best, r.notes, r.created_at, r.finished_at, r.errand_id, "
            "jsonb_array_length(r.offers) AS offers_count FROM compare_runs r",
            "SELECT count(*) AS n FROM compare_runs r",
            where,
            params,
            "r.created_at DESC, r.id",
            offset,
            limit,
        )

    def latest_compare(self, errand_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM compare_runs WHERE errand_id = %s ORDER BY created_at DESC LIMIT 1", (errand_id,)
        )

    def prune_compares(self, errand_id: str, keep: int = 50) -> None:
        with self.pool.connection(timeout=10) as conn:
            conn.execute(
                "DELETE FROM compare_runs WHERE errand_id = %s AND id NOT IN "
                "(SELECT id FROM compare_runs WHERE errand_id = %s ORDER BY created_at DESC LIMIT %s)",
                (errand_id, errand_id, keep),
            )
