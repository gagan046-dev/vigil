from __future__ import annotations

import fakeredis
import pytest

from vigil.config import Settings


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MIN_INTERVAL_MINUTES", "1")
    for var in ("NTFY_URL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "LLM_MODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def r():
    return fakeredis.FakeRedis(decode_responses=True)


@pytest.fixture
def settings():
    return Settings(
        env="test",
        internal_token="test-token-123",
        allowed_internal_hosts=("vigil-demo-shop",),
        dryrun_timeout_s=1,
        host_min_gap_ms=0,
    )
