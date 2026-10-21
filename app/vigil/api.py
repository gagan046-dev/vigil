"""Vigil HTTP API (FastAPI). Run as: uvicorn vigil.api:app --host 0.0.0.0 --port 8000"""

from __future__ import annotations

import hmac
import logging
import math
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field, ValidationError, field_validator

from . import planner, queue
from .compare import clean_reference, is_url
from .config import VERSION, Settings, load_settings
from .llm import LLMClient
from .metrics import API_PLANS_TOTAL, ERRANDS_CREATED_TOTAL
from .models import ErrandSpec, describe_condition
from .scheduler import make_scheduler, next_run_at, schedule_for
from .search import sellers_for

log = logging.getLogger("vigil.api")


@dataclass
class Deps:
    settings: Settings
    store: object
    r: object
    scheduler: object
    llm: LLMClient | None


class PlanIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DryRunIn(BaseModel):
    spec: ErrandSpec


class ErrandIn(BaseModel):
    spec: ErrandSpec
    prompt: str | None = Field(default=None, max_length=2000)


class ActiveIn(BaseModel):
    active: bool


class CompareIn(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    max_pages: int | None = Field(default=None, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def _check_query(cls, v: str) -> str:
        v = " ".join(v.split())
        if len(v) < 2:
            raise ValueError("enter a product name or a product URL")
        if v.lower().startswith(("http://", "https://")):
            if not urlsplit(v).hostname or not is_url(v):
                raise ValueError("the product URL is not valid")
        elif "://" in v:
            raise ValueError("only http(s) product URLs can be compared")
        return v


class TrackIn(BaseModel):
    threshold: float | None = Field(default=None, gt=0, lt=1e12)
    every_minutes: int = 60


MAX_PER_PAGE = 100
_HIDDEN = ("last_text", "last_hash")


def errand_status(errand: dict) -> str:
    if not errand.get("active", True):
        return "paused"
    return "failing" if int(errand.get("consecutive_failures") or 0) >= 3 else "active"


def _public(errand: dict) -> dict:
    out = {k: v for k, v in errand.items() if k not in _HIDDEN}
    spec = errand.get("spec") or {}
    out["schedule"] = schedule_for(errand)
    out["condition"] = describe_condition(spec)
    out["failing"] = int(errand.get("consecutive_failures") or 0) >= 3
    out["status"] = errand_status(errand)
    prev, last = errand.get("prev_value"), errand.get("last_value")
    out["prev_value"] = prev
    out["change"] = round(last - prev, 2) if prev is not None and last is not None else None
    return out


def paged(items: list, total: int, page: int, per_page: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, math.ceil(total / per_page)),
    }


def _offset(page: int, per_page: int) -> int:
    return (page - 1) * per_page


def _reconcile_loop(deps: Deps, attempts: int = 20) -> None:
    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            result = deps.scheduler.reconcile(deps.store.list_errands())
            log.info("schedules reconciled: %s", result)
            return
        except Exception as exc:
            log.warning("schedule reconcile failed (attempt %d): %s", attempt, type(exc).__name__)
            time.sleep(delay)
            delay = min(delay * 2, 60)


def create_app(
    settings: Settings | None = None, store=None, r=None, scheduler=None, llm=None, background: bool = True
) -> FastAPI:
    holder: dict[str, Deps] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        s = settings or load_settings()
        st = store
        if st is None:
            from .db import PgStore

            st = PgStore(s.database_url)
        rr = r if r is not None else queue.connect(s.redis_url)
        sch = scheduler if scheduler is not None else make_scheduler(s, st, rr)
        lm = llm if llm is not None else LLMClient(s, rr)
        deps = Deps(s, st, rr, sch, lm)
        holder["deps"] = deps
        app.state.deps = deps
        if background:
            threading.Thread(target=_reconcile_loop, args=(deps,), name="reconcile", daemon=True).start()
            sch.start()
        yield
        sch.stop()

    app = FastAPI(
        title="Vigil API",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    def get_deps(request: Request) -> Deps:
        return request.app.state.deps

    def _errand_or_404(deps: Deps, errand_id: uuid.UUID) -> dict:
        errand = deps.store.get_errand(str(errand_id))
        if errand is None:
            raise HTTPException(404, "errand not found")
        return errand

    # ------------------------------------------------------------ health

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/readyz")
    def readyz(deps: Deps = Depends(get_deps)) -> JSONResponse:
        checks = {}
        for name, fn in (("db", deps.store.ping), ("schema", deps.store.schema_present), ("redis", deps.r.ping)):
            try:
                checks[name] = bool(fn())
            except Exception:
                checks[name] = False
        ok = all(checks.values())
        return JSONResponse({"ok": ok, **checks}, status_code=200 if ok else 503)

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    # ------------------------------------------------------------ planning

    @app.post("/api/plan")
    def plan(body: PlanIn, deps: Deps = Depends(get_deps)) -> dict:
        try:
            result = planner.plan(body.text, deps.llm)
        except planner.PlanError as exc:
            raise HTTPException(422, str(exc)) from exc
        API_PLANS_TOTAL.labels(source=result.source).inc()
        return {"spec": result.spec.model_dump(), "source": result.source, "notes": result.notes}

    @app.post("/api/dryrun")
    def dryrun(body: DryRunIn, deps: Deps = Depends(get_deps)) -> dict:
        reply = queue.push_dryrun(deps.r, body.spec.model_dump(), deps.settings.dryrun_timeout_s)
        result = queue.wait_reply(deps.r, reply, deps.settings.dryrun_timeout_s)
        if result is None:
            raise HTTPException(504, "No worker answered in time. Is a worker running?")
        return result

    # ------------------------------------------------------------ errands

    def _create(deps: Deps, spec: ErrandSpec, prompt: str | None) -> dict:
        errand = deps.store.create_errand(spec.name, spec.model_dump(), prompt)
        ERRANDS_CREATED_TOTAL.inc()
        try:
            deps.scheduler.upsert(errand)
        except Exception as exc:  # the startup reconcile will retry it
            log.warning("could not create schedule: %s", type(exc).__name__)
        queue.enqueue_check(deps.r, errand["id"])
        return _public(errand)

    @app.post("/api/errands", status_code=201)
    def create_errand(body: ErrandIn, deps: Deps = Depends(get_deps)) -> dict:
        return _create(deps, body.spec, body.prompt)

    @app.get("/api/errands")
    def list_errands(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
        q: str | None = Query(None, max_length=200),
        status: Literal["active", "paused", "failing"] | None = None,
        deps: Deps = Depends(get_deps),
    ) -> dict:
        items, total = deps.store.page_errands(_offset(page, per_page), per_page, (q or "").strip() or None, status)
        return paged([_public(e) for e in items], total, page, per_page)

    @app.get("/api/errands/{errand_id}")
    def errand_detail(errand_id: uuid.UUID, deps: Deps = Depends(get_deps)) -> dict:
        errand = _errand_or_404(deps, errand_id)
        out = _public(errand)
        out["next_run_at"] = next_run_at(
            errand, getattr(deps.scheduler, "name", deps.settings.scheduler), deps.settings.schedule_tz
        )
        out["latest_compare"] = None
        if (errand.get("spec") or {}).get("kind") == "best_price_below":
            run = deps.store.latest_compare(str(errand_id))
            if run:
                out["latest_compare"] = {k: run.get(k) for k in ("id", "status", "best", "product", "created_at")}
        return out

    @app.get("/api/errands/{errand_id}/history")
    def errand_history(
        errand_id: uuid.UUID, days: int = Query(30, ge=1, le=365), deps: Deps = Depends(get_deps)
    ) -> list[dict]:
        _errand_or_404(deps, errand_id)
        return [
            {"at": c["at"], "value": c["value"], "status": c["status"]}
            for c in deps.store.errand_history(str(errand_id), days)
        ]

    @app.patch("/api/errands/{errand_id}")
    def patch_errand(errand_id: uuid.UUID, body: ActiveIn, deps: Deps = Depends(get_deps)) -> dict:
        _errand_or_404(deps, errand_id)
        errand = deps.store.set_active(str(errand_id), body.active)
        if errand is None:  # deleted meanwhile
            raise HTTPException(404, "errand not found")
        try:
            deps.scheduler.upsert(errand)
        except Exception as exc:
            log.warning("could not update schedule: %s", type(exc).__name__)
            raise HTTPException(502, "saved, but the schedule could not be updated; retry") from exc
        return _public(errand)

    @app.delete("/api/errands/{errand_id}", status_code=204)
    def delete_errand(errand_id: uuid.UUID, deps: Deps = Depends(get_deps)) -> Response:
        _errand_or_404(deps, errand_id)
        deps.store.delete_errand(str(errand_id))
        try:
            deps.scheduler.delete(str(errand_id))
        except Exception as exc:  # orphan CronJobs are removed by reconcile
            log.warning("could not delete schedule: %s", type(exc).__name__)
        return Response(status_code=204)

    @app.post("/api/errands/{errand_id}/check", status_code=202)
    def check_now(errand_id: uuid.UUID, deps: Deps = Depends(get_deps)) -> dict:
        _errand_or_404(deps, errand_id)
        return {"queued": queue.enqueue_check(deps.r, str(errand_id))}

    @app.get("/api/errands/{errand_id}/checks")
    def errand_checks(
        errand_id: uuid.UUID,
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
        deps: Deps = Depends(get_deps),
    ) -> dict:
        _errand_or_404(deps, errand_id)
        items, total = deps.store.page_checks(str(errand_id), _offset(page, per_page), per_page)
        return paged(items, total, page, per_page)

    @app.get("/api/log")
    def log_feed(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
        status: Literal["baseline", "unchanged", "changed", "triggered", "error"] | None = None,
        errand_id: uuid.UUID | None = None,
        deps: Deps = Depends(get_deps),
    ) -> dict:
        eid = str(errand_id) if errand_id else None
        items, total = deps.store.page_log(_offset(page, per_page), per_page, status, eid)
        return paged(items, total, page, per_page)

    # ------------------------------------------------------------ inbox

    @app.get("/api/inbox")
    def inbox(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
        unread: bool | None = None,
        deps: Deps = Depends(get_deps),
    ) -> dict:
        items, total = deps.store.page_inbox(_offset(page, per_page), per_page, unread)
        return paged(items, total, page, per_page)

    @app.get("/api/inbox/unread-count")
    def inbox_unread_count(deps: Deps = Depends(get_deps)) -> dict:
        return {"count": deps.store.unread_count()}

    @app.post("/api/inbox/read-all")
    def mark_all_read(deps: Deps = Depends(get_deps)) -> dict:
        return {"ok": True, "updated": deps.store.mark_all_read()}

    @app.post("/api/inbox/{notification_id}/read")
    def mark_read(notification_id: int, deps: Deps = Depends(get_deps)) -> dict:
        if not deps.store.mark_read(notification_id):
            raise HTTPException(404, "notification not found")
        return {"ok": True}

    # ------------------------------------------------------------ price comparison

    def _compare_or_404(deps: Deps, compare_id: uuid.UUID) -> dict:
        run = deps.store.get_compare(str(compare_id))
        if run is None:
            raise HTTPException(404, "comparison not found")
        return run

    @app.post("/api/compare", status_code=202)
    def start_compare(body: CompareIn, deps: Deps = Depends(get_deps)) -> dict:
        run = deps.store.create_compare(body.query, notes={"max_pages": body.max_pages} if body.max_pages else {})
        queue.enqueue_compare(deps.r, run["id"], body.max_pages)
        return {"id": run["id"], "status": run["status"]}

    @app.get("/api/compare")
    def list_compares(
        page: int = Query(1, ge=1),
        per_page: int = Query(25, ge=1, le=MAX_PER_PAGE),
        errand_id: uuid.UUID | None = None,
        deps: Deps = Depends(get_deps),
    ) -> dict:
        eid = str(errand_id) if errand_id else None
        items, total = deps.store.page_compares(_offset(page, per_page), per_page, eid)
        return paged(items, total, page, per_page)

    @app.get("/api/compare/{compare_id}")
    def get_compare(compare_id: uuid.UUID, deps: Deps = Depends(get_deps)) -> dict:
        return _compare_or_404(deps, compare_id)

    @app.post("/api/compare/{compare_id}/track", status_code=201)
    def track_compare(compare_id: uuid.UUID, body: TrackIn, deps: Deps = Depends(get_deps)) -> dict:
        run = _compare_or_404(deps, compare_id)
        if run.get("status") != "done":
            raise HTTPException(409, "the comparison has not finished yet")
        product = run.get("product") or {}
        source_url = product.get("url") or (run["query"] if is_url(run["query"]) else None)
        raw_title = product.get("title") or ("" if is_url(run["query"]) else run["query"])
        title = clean_reference(raw_title) if raw_title else None
        label = title or (urlsplit(source_url).hostname if source_url else run["query"])
        name = f"Best price: {label}"
        try:
            spec = ErrandSpec(
                name=name if len(name) <= 80 else name[:77] + "...",
                kind="best_price_below",
                query=title[:300] if title else None,
                url=source_url,
                threshold=body.threshold,
                every_minutes=body.every_minutes,
            )
        except ValidationError as exc:
            raise HTTPException(422, exc.errors()[0].get("msg", "invalid watch")) from exc
        return _create(deps, spec, f"Track the best price of {label}")

    # ------------------------------------------------------------ system

    @app.get("/api/system")
    def system(deps: Deps = Depends(get_deps)) -> dict:
        s = deps.settings
        try:
            used = queue.llm_budget_used(deps.r)
            depth = queue.queue_depth(deps.r)
        except Exception:
            used, depth = None, None
        return {
            "version": s.version,
            "env": s.env,
            "llm_mode": s.llm_mode,
            "llm_budget_used": used,
            "llm_daily_budget": s.llm_daily_budget,
            "queue_depth": depth,
            "scheduler": getattr(deps.scheduler, "name", s.scheduler),
            "min_interval_minutes": s.min_interval_minutes,
            "search_provider": s.search_provider,
            "compare_sellers": [{"key": x.key, "name": x.name, "domain": x.domain} for x in sellers_for(s)],
            "compare_max_pages": s.compare_max_pages,
        }

    # ------------------------------------------------------------ internal

    @app.post("/internal/trigger/{errand_id}", status_code=202)
    def trigger(
        errand_id: uuid.UUID, authorization: str | None = Header(default=None), deps: Deps = Depends(get_deps)
    ) -> dict:
        token = deps.settings.internal_token
        scheme, _, supplied = (authorization or "").partition(" ")
        if (
            not token
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied.strip().encode(), token.encode())
        ):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})
        errand = _errand_or_404(deps, errand_id)
        if not errand.get("active", True):
            return {"queued": False, "reason": "paused"}
        return {"queued": queue.enqueue_check(deps.r, str(errand_id))}

    return app


app = create_app()
