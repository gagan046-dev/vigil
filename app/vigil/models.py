"""The ErrandSpec: the structured form of a plain-English watch request."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import min_interval_minutes

ALLOWED_INTERVALS: tuple[int, ...] = (1, 2, 5, 10, 15, 30, 60, 120, 180, 360, 720, 1440)

Kind = Literal["price_below", "price_above", "text_appears", "text_disappears", "page_changed"]
PRICE_KINDS = ("price_below", "price_above")
TEXT_KINDS = ("text_appears", "text_disappears")


def snap_interval(minutes: float | int) -> int:
    """Snap any positive number of minutes to the nearest allowed interval.

    Ties go to the larger (more polite) interval.
    """
    m = max(1.0, float(minutes))
    best = ALLOWED_INTERVALS[0]
    for candidate in ALLOWED_INTERVALS:
        if abs(candidate - m) <= abs(best - m):
            best = candidate
    return best


def cron_for(every_minutes: int) -> str:
    """Convert an allowed interval into a cron expression."""
    n = snap_interval(every_minutes)
    if n < 60:
        return f"*/{n} * * * *"
    if n == 60:
        return "0 * * * *"
    if n < 1440:
        return f"0 */{n // 60} * * *"
    return "0 8 * * *"


class ErrandSpec(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=80)
    url: str = Field(max_length=2000)
    kind: Kind
    threshold: float | None = None
    keyword: str | None = Field(default=None, max_length=120)
    selector: str | None = Field(default=None, max_length=300)
    render: bool = False
    every_minutes: int = 60

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        parts = urlsplit(v)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("url must be an http(s) URL with a host")
        return v

    @field_validator("keyword", "selector")
    @classmethod
    def _blank_to_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("every_minutes", mode="before")
    @classmethod
    def _snap(cls, v):
        try:
            return snap_interval(float(v))
        except (TypeError, ValueError) as exc:
            raise ValueError("every_minutes must be a number") from exc

    @model_validator(mode="after")
    def _check_kind_fields(self) -> ErrandSpec:
        if self.kind in PRICE_KINDS:
            if self.threshold is None or self.threshold <= 0:
                raise ValueError("threshold must be a positive number for price watches")
        elif self.kind in TEXT_KINDS and not self.keyword:
            raise ValueError("keyword is required for text watches")
        floor = min_interval_minutes()
        if self.every_minutes < floor:
            raise ValueError(f"every_minutes must be at least {floor}")
        return self

    @property
    def host(self) -> str:
        return (urlsplit(self.url).hostname or "").lower()

    @property
    def cron(self) -> str:
        return cron_for(self.every_minutes)


def describe_condition(spec: dict) -> str:
    """Short human description, e.g. 'price < 1,20,000'."""
    from .extract import format_number

    kind = spec.get("kind")
    if kind == "price_below":
        return f"price < {format_number(spec.get('threshold') or 0)}"
    if kind == "price_above":
        return f"price > {format_number(spec.get('threshold') or 0)}"
    if kind == "text_appears":
        return f'"{spec.get("keyword")}" appears'
    if kind == "text_disappears":
        return f'"{spec.get("keyword")}" disappears'
    return "page changes"
