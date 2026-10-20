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
