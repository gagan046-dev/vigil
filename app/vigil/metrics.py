"""Prometheus metrics shared by all Vigil processes."""

from __future__ import annotations

from prometheus_client import Counter, Histogram

CHECKS_TOTAL = Counter("vigil_checks_total", "Checks performed by workers", ["status"])
LLM_CALLS_TOTAL = Counter("vigil_llm_calls_total", "LLM calls attempted", ["purpose"])
FETCH_SECONDS = Histogram(
    "vigil_fetch_seconds",
    "Time spent fetching pages",
    ["mode"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 30, 60),
)
NOTIFICATIONS_TOTAL = Counter("vigil_notifications_total", "Notifications delivered", ["channel"])
API_PLANS_TOTAL = Counter("vigil_api_plans_total", "Plans produced by the API", ["source"])
ERRANDS_CREATED_TOTAL = Counter("vigil_errands_created_total", "Errands created")
