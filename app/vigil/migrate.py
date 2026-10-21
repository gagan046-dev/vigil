"""Idempotent schema migration. Run as: python -m vigil.migrate

Safe to run from several pods at once (pg_advisory_lock) and repeatedly
(CREATE ... IF NOT EXISTS).
"""

from __future__ import annotations

import logging
import sys
import time

import psycopg

from .config import load_settings

log = logging.getLogger("vigil.migrate")

LOCK_ID = 7_351_001  # arbitrary constant shared by all Vigil migrators

SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS errands (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        name text NOT NULL,
        prompt text,
        spec jsonb NOT NULL,
        active boolean NOT NULL DEFAULT true,
        created_at timestamptz NOT NULL DEFAULT now(),
        last_checked_at timestamptz,
        last_status text,
        last_value double precision,
        last_hash text,
        last_text text,
        condition_met boolean NOT NULL DEFAULT false,
        consecutive_failures integer NOT NULL DEFAULT 0,
        selector_learned text
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS checks (
        id bigserial PRIMARY KEY,
        errand_id uuid NOT NULL REFERENCES errands(id) ON DELETE CASCADE,
        at timestamptz NOT NULL DEFAULT now(),
        duration_ms integer,
        status text NOT NULL CHECK (status IN ('baseline','unchanged','changed','triggered','error')),
        value double precision,
        rendered boolean NOT NULL DEFAULT false,
        llm_used boolean NOT NULL DEFAULT false,
        message text
    )
    """,
    "CREATE INDEX IF NOT EXISTS checks_errand_at_idx ON checks (errand_id, at DESC)",
    "CREATE INDEX IF NOT EXISTS checks_at_idx ON checks (at DESC)",
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id bigserial PRIMARY KEY,
        errand_id uuid REFERENCES errands(id) ON DELETE SET NULL,
        at timestamptz NOT NULL DEFAULT now(),
        title text NOT NULL,
        body text NOT NULL DEFAULT '',
        read boolean NOT NULL DEFAULT false,
        delivered_to text
    )
    """,
    "CREATE INDEX IF NOT EXISTS notifications_at_idx ON notifications (at DESC)",
    "CREATE INDEX IF NOT EXISTS notifications_unread_idx ON notifications (at DESC) WHERE NOT read",
    """
    CREATE TABLE IF NOT EXISTS compare_runs (
        id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        query text NOT NULL,
        status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued','searching','fetching','done','error')),
        product jsonb,
        offers jsonb NOT NULL DEFAULT '[]'::jsonb,
        best jsonb,
        notes jsonb NOT NULL DEFAULT '{}'::jsonb,
        created_at timestamptz NOT NULL DEFAULT now(),
        finished_at timestamptz,
        errand_id uuid REFERENCES errands(id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS compare_runs_created_idx ON compare_runs (created_at DESC)",
    "CREATE INDEX IF NOT EXISTS compare_runs_errand_idx ON compare_runs (errand_id, created_at DESC)",
]

TABLES = ("errands", "checks", "notifications", "compare_runs")


def migrate(conninfo: str, attempts: int = 30, delay_s: float = 2.0) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with psycopg.connect(conninfo, autocommit=True) as conn:
                conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
                try:
                    for stmt in SCHEMA:
                        conn.execute(stmt)
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
            log.info("schema is up to date")
            return
        except psycopg.OperationalError as exc:
            last_exc = exc
            log.warning("database not ready (attempt %d/%d): %s", attempt, attempts, type(exc).__name__)
            time.sleep(delay_s)
    raise SystemExit(f"migration failed: {last_exc}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    migrate(load_settings().database_url)


if __name__ == "__main__":
    main()
    sys.exit(0)
