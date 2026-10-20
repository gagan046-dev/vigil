"""Redis helpers: the check queue, dry-run replies, notifications, budgets, politeness."""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime

import redis

CHECKS = "vigil:checks"
QUEUED = "vigil:queued"
NOTIFY = "vigil:notify"
REPLY_PREFIX = "vigil:reply:"


def connect(url: str) -> redis.Redis:
    return redis.Redis.from_url(
        url, decode_responses=True, socket_timeout=90, socket_connect_timeout=5, health_check_interval=30
    )


def enqueue_check(r: redis.Redis, errand_id: str) -> bool:
    """Queue a check unless one is already waiting. Returns True if queued."""
    added = r.sadd(QUEUED, errand_id)
    if not added and r.llen(CHECKS) > 0:
        return False
    # (if the set says "queued" but the list is empty the marker is stale: queue anyway)
    r.rpush(CHECKS, json.dumps({"type": "check", "errand_id": errand_id}))
    return True


def push_dryrun(r: redis.Redis, spec: dict, timeout_s: int = 60) -> str:
    """Queue a dry run at the FRONT of the list (a person is waiting for it).

    expires_at lets a worker skip dry runs the API has already given up on.
    """
    reply = f"{REPLY_PREFIX}{uuid.uuid4()}"
    job = {"type": "dryrun", "spec": spec, "reply": reply, "expires_at": time.time() + timeout_s}
    r.lpush(CHECKS, json.dumps(job))
    return reply


def job_expired(job: dict, now: float | None = None) -> bool:
    try:
        exp = float(job.get("expires_at"))
    except (TypeError, ValueError):
        return False
    return (now if now is not None else time.time()) > exp


# keep BLPOP below the client's socket_timeout (90 s) so a long wait never looks like a dead socket
MAX_REPLY_WAIT_S = 80


def wait_reply(r: redis.Redis, reply_key: str, timeout_s: int) -> dict | None:
    item = r.blpop([reply_key], timeout=max(1, min(int(timeout_s), MAX_REPLY_WAIT_S)))
    if not item:
        return None
    return json.loads(item[1])


def push_reply(r: redis.Redis, reply_key: str, payload: dict) -> None:
    if not reply_key.startswith(REPLY_PREFIX):
        return
    pipe = r.pipeline()
    pipe.rpush(reply_key, json.dumps(payload, default=str))
    pipe.expire(reply_key, 120)
    pipe.execute()


def pop_job(r: redis.Redis, timeout_s: int = 5) -> dict | None:
    item = r.blpop([CHECKS], timeout=timeout_s)
    if not item:
        return None
    try:
        job = json.loads(item[1])
    except (ValueError, TypeError):
        return None
    if not isinstance(job, dict):
        return None
    if job.get("type") == "check" and job.get("errand_id"):
        r.srem(QUEUED, job["errand_id"])
    return job


def queue_depth(r: redis.Redis) -> int:
    return int(r.llen(CHECKS))


def push_notify(r: redis.Redis, errand_id: str | None, title: str, body: str) -> None:
    r.rpush(NOTIFY, json.dumps({"errand_id": errand_id, "title": title, "body": body}))


def pop_notify(r: redis.Redis, timeout_s: int = 5) -> dict | None:
    item = r.blpop([NOTIFY], timeout=timeout_s)
    if not item:
        return None
    try:
        msg = json.loads(item[1])
    except (ValueError, TypeError):
        return None
    return msg if isinstance(msg, dict) else None


# ---------------------------------------------------------------- LLM budget


def budget_key(now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    return f"vigil:llm:{now:%Y%m%d}"


def llm_budget_used(r: redis.Redis) -> int:
    raw = r.get(budget_key())
    return int(raw) if raw else 0


def llm_budget_take(r: redis.Redis, daily_budget: int) -> bool:
    """Reserve one LLM call from today's budget. False if exhausted."""
    key = budget_key()
    pipe = r.pipeline()
    pipe.incr(key)
    pipe.expire(key, 2 * 24 * 3600)
    used, _ = pipe.execute()
    return int(used) <= daily_budget


# ---------------------------------------------------------------- politeness


def wait_host_slot(r: redis.Redis, host: str, gap_ms: int, max_wait_s: float = 10.0, sleep=time.sleep) -> bool:
    """Wait until at least gap_ms passed since the last fetch of host (across workers).

    Returns True when the slot was acquired, False when we gave up after max_wait_s
    (the fetch then proceeds anyway; better late than never).
    """
    if gap_ms <= 0:
        return True
    key = f"vigil:host:{host}"
    deadline = time.monotonic() + max_wait_s
    while True:
        if r.set(key, "1", nx=True, px=gap_ms):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        ttl_ms = r.pttl(key)
        wait = (ttl_ms / 1000.0) if ttl_ms and ttl_ms > 0 else 0.1
        sleep(max(0.05, min(wait, remaining, 1.0)))
