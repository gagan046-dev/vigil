"""Schedules: one Kubernetes CronJob per errand (or an in-process ticker for local runs)."""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

from . import queue
from .config import Settings
from .models import cron_for, snap_interval

log = logging.getLogger(__name__)

NS_FILE = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
LABEL_ERRAND = "vigil.dev/errand-id"
BASE_LABELS = {"app.kubernetes.io/part-of": "vigil", "app.kubernetes.io/managed-by": "vigil-api"}


def cronjob_name(errand_id: str) -> str:
    return "errand-" + errand_id.replace("-", "")[:12]


def _every(errand: dict) -> int:
    return snap_interval((errand.get("spec") or {}).get("every_minutes", 60))


def schedule_for(errand: dict) -> str:
    return cron_for(_every(errand))


def build_cronjob(errand: dict, settings: Settings) -> dict:
    """The CronJob manifest for one errand (plain dict; the k8s client accepts it as a body)."""
    eid = errand["id"]
    labels = {**BASE_LABELS, LABEL_ERRAND: eid}
    pod_labels = {**labels, "app.kubernetes.io/component": "errand-trigger"}
    container = {
        "name": "trigger",
        "image": settings.trigger_image,
        "imagePullPolicy": "IfNotPresent",
        "args": [
            "-fsS",
            "-m",
            "10",
            # A brand-new pod can start before the NetworkPolicy controller has
            # allowed its IP (k3s/kube-router), so the first connect may be refused.
            # Retry briefly; the check queue de-duplicates, so a retry is harmless.
            "--retry",
            "5",
            "--retry-delay",
            "2",
            "--retry-connrefused",
            "-X",
            "POST",
            "-H",
            "Authorization: Bearer $(VIGIL_INTERNAL_TOKEN)",
            f"$(VIGIL_API_URL)/internal/trigger/{eid}",
        ],
        "env": [
            {
                "name": "VIGIL_INTERNAL_TOKEN",
                "valueFrom": {"secretKeyRef": {"name": settings.internal_token_secret, "key": "internal-token"}},
            },
            {"name": "VIGIL_API_URL", "value": settings.trigger_api_url},
        ],
        "securityContext": {
            "runAsNonRoot": True,
            "runAsUser": 100,
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": True,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        },
        "resources": {
            "requests": {"cpu": "10m", "memory": "16Mi"},
            "limits": {"cpu": "100m", "memory": "32Mi"},
        },
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "CronJob",
        "metadata": {"name": cronjob_name(eid), "labels": labels},
        "spec": {
            "schedule": schedule_for(errand),
            "timeZone": settings.schedule_tz,
            "concurrencyPolicy": "Forbid",
            "startingDeadlineSeconds": 120,
            "successfulJobsHistoryLimit": 1,
            "failedJobsHistoryLimit": 1,
            "suspend": not errand.get("active", True),
            "jobTemplate": {
                "metadata": {"labels": labels},
                "spec": {
                    "backoffLimit": 1,
                    "activeDeadlineSeconds": 60,
                    "ttlSecondsAfterFinished": 600,
                    "template": {
                        "metadata": {"labels": pod_labels},
                        "spec": {
                            "restartPolicy": "Never",
                            "automountServiceAccountToken": False,
                            "securityContext": {
                                "runAsNonRoot": True,
                                "runAsUser": 100,
                                "seccompProfile": {"type": "RuntimeDefault"},
                            },
                            "containers": [container],
                        },
                    },
                },
            },
        },
    }


class NoneScheduler:
    name = "none"

    def upsert(self, errand: dict) -> None:
        return None

    def delete(self, errand_id: str) -> None:
        return None

    def reconcile(self, errands: list[dict]) -> dict:
        return {"created": 0, "deleted": 0}

    def start(self) -> None:
        return None

    def stop(self) -> None:
        return None


class KubernetesScheduler(NoneScheduler):
    name = "kubernetes"

    def __init__(self, settings: Settings, batch_api=None, namespace: str | None = None):
        self.settings = settings
        self._api = batch_api
        self._namespace = namespace

    @property
    def namespace(self) -> str:
        if self._namespace is None:
            try:
                self._namespace = NS_FILE.read_text().strip()
            except OSError:
                self._namespace = "default"
        return self._namespace

    @property
    def api(self):
        if self._api is None:
            from kubernetes import client, config

            config.load_incluster_config()
            self._api = client.BatchV1Api()
        return self._api

    @staticmethod
    def _status(exc: Exception) -> int | None:
        return getattr(exc, "status", None)

    def upsert(self, errand: dict) -> None:
        body = build_cronjob(errand, self.settings)
        name = body["metadata"]["name"]
        try:
            self.api.create_namespaced_cron_job(self.namespace, body)
            log.info("created CronJob %s", name)
        except Exception as exc:
            if self._status(exc) != 409:
                raise
            self.api.replace_namespaced_cron_job(name, self.namespace, body)

    def delete(self, errand_id: str) -> None:
        self._delete_name(cronjob_name(errand_id))

    def _delete_name(self, name: str) -> None:
        try:
            self.api.delete_namespaced_cron_job(name, self.namespace, propagation_policy="Background")
        except Exception as exc:
            if self._status(exc) != 404:
                raise

    def reconcile(self, errands: list[dict]) -> dict:
        wanted = {e["id"]: e for e in errands}
        existing = self.api.list_namespaced_cron_job(self.namespace, label_selector=LABEL_ERRAND)
        deleted = 0
        present: set[str] = set()
        for cj in existing.items:
            eid = (cj.metadata.labels or {}).get(LABEL_ERRAND)
            if eid not in wanted:
                self._delete_name(cj.metadata.name)  # another api replica may have deleted it already
                deleted += 1
            else:
                present.add(eid)
        created = 0
        for eid, errand in wanted.items():
            self.upsert(errand)  # also refreshes drifted specs (e.g. a new TRIGGER_IMAGE)
            created += eid not in present
        return {"created": created, "deleted": deleted}


class InternalScheduler(NoneScheduler):
    """For local runs/tests: a thread that enqueues due errands every 30 s."""

    name = "internal"

    def __init__(self, store, r, interval_s: float = 30.0):
        self.store = store
        self.r = r
        self.interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def due(self, now: datetime | None = None) -> list[dict]:
        now = now or datetime.now(UTC)
        out = []
        for e in self.store.list_errands():
            if not e.get("active", True):
                continue
            last = e.get("last_checked_at")
            if last is None or (now - last).total_seconds() >= _every(e) * 60 - 5:
                out.append(e)
        return out

    def tick(self) -> int:
        n = 0
        for e in self.due():
            n += queue.enqueue_check(self.r, e["id"])
        return n

    def _run(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                self.tick()
            except Exception:
                log.warning("internal scheduler tick failed", exc_info=True)

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="internal-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def make_scheduler(settings: Settings, store=None, r=None):
    if settings.scheduler == "kubernetes":
        return KubernetesScheduler(settings)
    if settings.scheduler == "internal":
        return InternalScheduler(store, r)
    return NoneScheduler()
