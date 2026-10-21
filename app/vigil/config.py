"""Runtime settings, read from environment variables.

Every component (api, worker, notifier, migrate) reads the same Settings so a
single Kubernetes ConfigMap + Secret configures the whole system.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from psycopg.conninfo import make_conninfo

VERSION = "0.1.0"


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.environ.get(name, default)
    return tuple(h.strip().lower() for h in raw.split(",") if h.strip())


DEFAULT_COMPARE_SELLERS = (
    "flipkart.com,amazon.in,myntra.com,croma.com,reliancedigital.in,vijaysales.com,tatacliq.com,ajio.com"
)


def min_interval_minutes() -> int:
    """Smallest schedule allowed (read live so tests/env changes apply)."""
    return max(1, _int("MIN_INTERVAL_MINUTES", 5))


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    return make_conninfo(
        host=os.environ.get("POSTGRES_HOST", "vigil-postgres"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "vigil"),
        user=os.environ.get("POSTGRES_USER", "vigil"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout="5",
    )


@dataclass(frozen=True)
class Settings:
    env: str = "dev"
    version: str = VERSION
    database_url: str = ""
    redis_url: str = "redis://vigil-redis:6379/0"
    # LLM
    llm_mode: str = "fake"
    llm_base_url: str = "http://vigil-llm-gateway:4000/v1"
    llm_model: str = "vigil-smart"
    llm_api_key: str = field(default="", repr=False)
    llm_daily_budget: int = 40
    # scheduling
    scheduler: str = "none"
    schedule_tz: str = "Asia/Kolkata"
    trigger_image: str = "curlimages/curl:8.22.0"
    trigger_api_url: str = "http://vigil-api:8000"
    internal_token_secret: str = "vigil-secrets"  # noqa: S105 (a Secret *name*, not a value)
    internal_token: str = field(default="", repr=False)
    # fetching
    allowed_internal_hosts: tuple[str, ...] = ()
    host_min_gap_ms: int = 5000
    dryrun_timeout_s: int = 60
    # notifications
    ntfy_url: str = ""
    telegram_bot_token: str = field(default="", repr=False)
    telegram_chat_id: str = ""
    # price comparison
    search_provider: str = "demo"  # demo | searxng
    searxng_url: str = "http://vigil-searxng:8080"
    compare_sellers: tuple[str, ...] = tuple(DEFAULT_COMPARE_SELLERS.split(","))
    compare_max_pages: int = 8
    demo_shop_url: str = "http://vigil-demo-shop:8000"
    # metrics
    worker_metrics_port: int = 9100
    notifier_metrics_port: int = 9101

    @property
    def min_interval_minutes(self) -> int:
        return min_interval_minutes()


def load_settings() -> Settings:
    e = os.environ.get
    return Settings(
        env=e("VIGIL_ENV", "dev"),
        database_url=_database_url(),
        redis_url=e("REDIS_URL", "redis://vigil-redis:6379/0"),
        llm_mode=e("LLM_MODE", "fake").strip().lower(),
        llm_base_url=e("LLM_BASE_URL", "http://vigil-llm-gateway:4000/v1").rstrip("/"),
        llm_model=e("LLM_MODEL", "vigil-smart"),
        llm_api_key=e("LLM_API_KEY", ""),
        llm_daily_budget=_int("LLM_DAILY_BUDGET", 40),
        scheduler=e("SCHEDULER", "none").strip().lower(),
        schedule_tz=e("SCHEDULE_TZ", "Asia/Kolkata"),
        trigger_image=e("TRIGGER_IMAGE", "curlimages/curl:8.22.0"),
        trigger_api_url=e("TRIGGER_API_URL", "http://vigil-api:8000").rstrip("/"),
        internal_token_secret=e("INTERNAL_TOKEN_SECRET", "vigil-secrets"),
        internal_token=e("VIGIL_INTERNAL_TOKEN", ""),
        allowed_internal_hosts=_csv("ALLOWED_INTERNAL_HOSTS"),
        host_min_gap_ms=_int("HOST_MIN_GAP_MS", 5000),
        dryrun_timeout_s=_int("DRYRUN_TIMEOUT_SECONDS", 60),
        ntfy_url=e("NTFY_URL", "").strip(),
        telegram_bot_token=e("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=e("TELEGRAM_CHAT_ID", "").strip(),
        search_provider=e("SEARCH_PROVIDER", "demo").strip().lower() or "demo",
        searxng_url=e("SEARXNG_URL", "http://vigil-searxng:8080").strip().rstrip("/"),
        compare_sellers=_csv("COMPARE_SELLERS", DEFAULT_COMPARE_SELLERS),
        compare_max_pages=max(1, min(20, _int("COMPARE_MAX_PAGES", 8))),
        demo_shop_url=e("DEMO_SHOP_URL", "http://vigil-demo-shop:8000").strip().rstrip("/"),
        worker_metrics_port=_int("WORKER_METRICS_PORT", 9100),
        notifier_metrics_port=_int("NOTIFIER_METRICS_PORT", 9101),
    )
