"""Vigil HTTP API (FastAPI). Run as: uvicorn vigil.api:app --host 0.0.0.0 --port 8000"""

from __future__ import annotations

import hmac
import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import planner, queue
from .config import VERSION, Settings, load_settings
from .llm import LLMClient
from .metrics import API_PLANS_TOTAL, ERRANDS_CREATED_TOTAL
from .models import ErrandSpec, describe_condition
from .scheduler import make_scheduler, schedule_for

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


_HIDDEN = ("last_text", "last_hash")


def _public(errand: dict) -> dict:
    out = {k: v for k, v in errand.items() if k not in _HIDDEN}
    spec = errand.get("spec") or {}
    out["schedule"] = schedule_for(errand)
    out["condition"] = describe_condition(spec)
    out["failing"] = int(errand.get("consecutive_failures") or 0) >= 3
    return out


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

    @app.post("/api/errands", status_code=201)
    def create_errand(body: ErrandIn, deps: Deps = Depends(get_deps)) -> dict:
        spec = body.spec.model_dump()
        errand = deps.store.create_errand(body.spec.name, spec, body.prompt)
        ERRANDS_CREATED_TOTAL.inc()
        try:
            deps.scheduler.upsert(errand)
        except Exception as exc:  # the startup reconcile will retry it
            log.warning("could not create schedule: %s", type(exc).__name__)
        queue.enqueue_check(deps.r, errand["id"])
        return _public(errand)

    @app.get("/api/errands")
    def list_errands(deps: Deps = Depends(get_deps)) -> list[dict]:
        return [_public(e) for e in deps.store.list_errands()]

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
        errand_id: uuid.UUID, limit: int = Query(50, ge=1, le=500), deps: Deps = Depends(get_deps)
    ) -> list[dict]:
        _errand_or_404(deps, errand_id)
        return deps.store.list_checks(str(errand_id), limit)

    @app.get("/api/log")
    def log_feed(limit: int = Query(100, ge=1, le=500), deps: Deps = Depends(get_deps)) -> list[dict]:
        return deps.store.recent_log(limit)

    # ------------------------------------------------------------ inbox

    @app.get("/api/inbox")
    def inbox(limit: int = Query(50, ge=1, le=500), deps: Deps = Depends(get_deps)) -> list[dict]:
        return deps.store.list_inbox(limit)

    @app.post("/api/inbox/{notification_id}/read")
    def mark_read(notification_id: int, deps: Deps = Depends(get_deps)) -> dict:
        if not deps.store.mark_read(notification_id):
            raise HTTPException(404, "notification not found")
        return {"ok": True}

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
