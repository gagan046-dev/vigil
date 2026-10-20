"""Tiny OpenAI-compatible client with a daily budget.

Attention routing: callers always try the deterministic path first and only ask
the LLM when it is genuinely needed. chat_json() returns None on *any* problem
(fake mode, budget exhausted, network error, unparsable reply) so callers can
fall back to deterministic logic without special cases.
"""

from __future__ import annotations

import json
import logging

import httpx

from . import queue
from .config import Settings
from .metrics import LLM_CALLS_TOTAL

log = logging.getLogger(__name__)


def first_json_object(text: str) -> dict | None:
    """Parse the first JSON object found in a model reply (handles ``` fences, prose)."""
    if not text:
        return None
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _ = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        idx = text.find("{", idx + 1)
    return None


class LLMClient:
    def __init__(self, settings: Settings, r=None, http: httpx.Client | None = None):
        self.settings = settings
        self.r = r
        self._http = http

    @property
    def mode(self) -> str:
        return self.settings.llm_mode

    def available(self) -> bool:
        if self.mode != "gateway":
            return False
        if self.r is None:
            return True
        try:
            return queue.llm_budget_used(self.r) < self.settings.llm_daily_budget
        except Exception:
            return False

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=60.0)
        return self._http

    def chat_json(self, system: str, user: str, purpose: str = "other") -> dict | None:
        if self.mode != "gateway":
            return None
        try:
            if self.r is not None and not queue.llm_budget_take(self.r, self.settings.llm_daily_budget):
                log.info("LLM daily budget exhausted; using deterministic fallback")
                return None
        except Exception:
            log.warning("could not check LLM budget; skipping LLM")
            return None
        LLM_CALLS_TOTAL.labels(purpose=purpose).inc()
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        body = {
            "model": self.settings.llm_model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            resp = self._client().post(f"{self.settings.llm_base_url}/chat/completions", json=body, headers=headers)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.warning("LLM call failed (%s): %s", purpose, type(exc).__name__)
            return None
        return first_json_object(content if isinstance(content, str) else "")
