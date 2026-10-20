from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tests.fakes import MemoryStore
from vigil import queue
from vigil.config import Settings
from vigil.scheduler import (
    InternalScheduler,
    KubernetesScheduler,
    build_cronjob,
    cronjob_name,
    make_scheduler,
)

EID = "3f2b8c1e-9d4a-4b7e-8f00-123456789abc"


def _errand(every=30, active=True, eid=EID):
    return {"id": eid, "name": "x", "active": active, "spec": {"every_minutes": every}}


def test_cronjob_name():
    assert cronjob_name(EID) == "errand-3f2b8c1e9d4a"


def test_build_cronjob_manifest():
    s = Settings(
        schedule_tz="Asia/Kolkata",
        trigger_image="curlimages/curl:8.22.0",
        trigger_api_url="http://vigil-api:8000",
        internal_token_secret="vigil-secrets",
    )
    cj = build_cronjob(_errand(30), s)
    assert cj["metadata"]["name"] == "errand-3f2b8c1e9d4a"
    labels = cj["metadata"]["labels"]
    assert labels == {
        "app.kubernetes.io/part-of": "vigil",
        "app.kubernetes.io/managed-by": "vigil-api",
        "vigil.dev/errand-id": EID,
    }
    spec = cj["spec"]
    assert spec["schedule"] == "*/30 * * * *"
    assert spec["timeZone"] == "Asia/Kolkata"
    assert spec["concurrencyPolicy"] == "Forbid"
    assert spec["startingDeadlineSeconds"] == 120
    assert spec["successfulJobsHistoryLimit"] == 1 and spec["failedJobsHistoryLimit"] == 1
    assert spec["suspend"] is False
    job = spec["jobTemplate"]["spec"]
    assert (job["backoffLimit"], job["activeDeadlineSeconds"], job["ttlSecondsAfterFinished"]) == (1, 60, 600)
    pod = job["template"]
    assert pod["metadata"]["labels"]["app.kubernetes.io/component"] == "errand-trigger"
    assert pod["spec"]["restartPolicy"] == "Never"
    assert pod["spec"]["automountServiceAccountToken"] is False
    c = pod["spec"]["containers"][0]
    assert c["image"] == "curlimages/curl:8.22.0"
    assert c["args"] == [
        "-fsS",
        "-m",
        "10",
        "--retry",
        "5",
        "--retry-delay",
        "2",
        "--retry-connrefused",
        "-X",
        "POST",
        "-H",
        "Authorization: Bearer $(VIGIL_INTERNAL_TOKEN)",
        f"$(VIGIL_API_URL)/internal/trigger/{EID}",
    ]
    env = {e["name"]: e for e in c["env"]}
    assert env["VIGIL_INTERNAL_TOKEN"]["valueFrom"]["secretKeyRef"] == {
        "name": "vigil-secrets",
        "key": "internal-token",
    }
    assert env["VIGIL_API_URL"]["value"] == "http://vigil-api:8000"
    sc = c["securityContext"]
    assert sc["runAsNonRoot"] and sc["runAsUser"] == 100 and sc["allowPrivilegeEscalation"] is False
    assert sc["capabilities"] == {"drop": ["ALL"]} and sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert sc["readOnlyRootFilesystem"] is True
    assert c["resources"] == {"requests": {"cpu": "10m", "memory": "16Mi"}, "limits": {"cpu": "100m", "memory": "32Mi"}}


def test_paused_errand_is_suspended_and_daily_cron():
    cj = build_cronjob(_errand(1440, active=False), Settings())
    assert cj["spec"]["suspend"] is True
    assert cj["spec"]["schedule"] == "0 8 * * *"


class ApiError(Exception):
    def __init__(self, status):
        self.status = status


class FakeBatch:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.calls = []

    def create_namespaced_cron_job(self, ns, body):
        self.calls.append(("create", ns, body["metadata"]["name"]))
        if body["metadata"]["name"] in self.items:
            raise ApiError(409)
        self.items[body["metadata"]["name"]] = body

    def replace_namespaced_cron_job(self, name, ns, body):
        self.calls.append(("replace", ns, name))
        self.items[name] = body

    def delete_namespaced_cron_job(self, name, ns, propagation_policy=None):
        self.calls.append(("delete", ns, name))
        if name not in self.items:
            raise ApiError(404)
        del self.items[name]

    def list_namespaced_cron_job(self, ns, label_selector=None):
        assert label_selector == "vigil.dev/errand-id"
        return SimpleNamespace(
            items=[
                SimpleNamespace(metadata=SimpleNamespace(name=n, labels=b["metadata"]["labels"]))
                for n, b in self.items.items()
            ]
        )


def test_kubernetes_upsert_update_delete():
    batch = FakeBatch()
    k = KubernetesScheduler(Settings(), batch_api=batch, namespace="vigil")
    k.upsert(_errand())
    k.upsert(_errand(active=False))  # conflict -> replace
    assert batch.items["errand-3f2b8c1e9d4a"]["spec"]["suspend"] is True
    assert [c[0] for c in batch.calls] == ["create", "create", "replace"]
    k.delete(EID)
    k.delete(EID)  # 404 ignored
    assert batch.items == {}


def test_reconcile_creates_missing_and_deletes_orphans():
    batch = FakeBatch()
    k = KubernetesScheduler(Settings(), batch_api=batch, namespace="vigil")
    orphan = "aaaaaaaa-0000-0000-0000-000000000000"
    k.upsert(_errand(eid=orphan))
    result = k.reconcile([_errand()])
    assert result == {"created": 1, "deleted": 1}
    assert list(batch.items) == ["errand-3f2b8c1e9d4a"]


def test_internal_scheduler_due(r):
    store = MemoryStore()
    a = store.create_errand("a", {"every_minutes": 5}, None)
    b = store.create_errand("b", {"every_minutes": 60}, None)
    c = store.create_errand("c", {"every_minutes": 5}, None)
    now = datetime.now(UTC)
    store.update_errand_state(a["id"], last_checked_at=now - timedelta(minutes=6))
    store.update_errand_state(b["id"], last_checked_at=now - timedelta(minutes=10))
    store.set_active(c["id"], False)
    sch = InternalScheduler(store, r)
    assert [e["id"] for e in sch.due(now)] == [a["id"]]
    assert sch.tick() == 1
    assert queue.queue_depth(r) == 1


def test_make_scheduler():
    assert make_scheduler(Settings(scheduler="none")).name == "none"
    assert make_scheduler(Settings(scheduler="kubernetes")).name == "kubernetes"
    assert make_scheduler(Settings(scheduler="internal"), MemoryStore(), None).name == "internal"


def test_reconcile_tolerates_orphan_already_deleted():
    batch = FakeBatch()
    k = KubernetesScheduler(Settings(), batch_api=batch, namespace="vigil")
    orphan = "bbbbbbbb-0000-0000-0000-000000000000"
    k.upsert(_errand(eid=orphan))
    real_delete = batch.delete_namespaced_cron_job

    def racing_delete(name, ns, propagation_policy=None):
        batch.items.pop(name, None)  # another api replica got there first
        return real_delete(name, ns, propagation_policy)

    batch.delete_namespaced_cron_job = racing_delete
    assert k.reconcile([]) == {"created": 0, "deleted": 1}
