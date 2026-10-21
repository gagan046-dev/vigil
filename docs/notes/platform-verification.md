# Platform verification notes

Raw notes for the docs writers: the exact commands run against the local k3d
cluster and the key outputs (trimmed). Cluster: k3d "vigil" (1 server + 2 agents,
k3s v1.35.5), ingress on http://*.localhost:8081, Kube API 127.0.0.1:6550.

## Phase 1 - raw Kubernetes manifests (namespace `vigil`)

Bring-up (PowerShell, from the repo root):

```powershell
k3d cluster create --config cluster/k3d-config.yaml
powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1      # copy local images into every node
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --version 2.21.0 -n keda --create-namespace
powershell -ExecutionPolicy Bypass -File scripts\phase1-up.ps1          # applies k8s/00..40 in order + waits
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1               # end-to-end demo with curl.exe
```

`phase1-up.ps1` runs, in order: `kubectl apply -f k8s/00-namespace.yaml`,
`01-configmap.yaml`, `scripts/create-dev-secret.ps1` (random values, never printed),
`10-postgres.yaml`, `11-redis.yaml` (+ `kubectl rollout status`), `20-api.yaml`,
`21-worker.yaml`, `22-notifier.yaml`, `24-demo-shop.yaml`, `23-frontend.yaml`,
`30-ingress.yaml`, `40-networkpolicies.yaml`.

`demo.ps1` runs: GET /api/system, POST demo-shop /admin/reset, POST /api/plan,
POST /api/dryrun, POST /api/errands (CronJob appears), POST /admin/price 114990,
then polls GET /api/inbox until the notification arrives.

### Quick re-run after the cluster was restarted (2026-10-21)

After `docker` restarted the k3d nodes, every pod recovered on its own within ~2 min
(api readiness waited for postgres/redis; one trigger Job failed while the api was
still starting, the next minute's Job completed).

```text
$ kubectl get nodes
NAME                 STATUS   ROLES           AGE   VERSION
k3d-vigil-agent-0    Ready    <none>          15h   v1.35.5+k3s1
k3d-vigil-agent-1    Ready    <none>          15h   v1.35.5+k3s1
k3d-vigil-server-0   Ready    control-plane   15h   v1.35.5+k3s1

$ kubectl get pods -n vigil
NAME                                 READY   STATUS      RESTARTS       AGE
errand-4714e86efbc1-29876137-xmq8w   0/1     Error       0              100s   <- fired while api was starting
errand-4714e86efbc1-29876138-5z5gf   0/1     Completed   0              32s
vigil-api-8554454f7b-klhh2           1/1     Running     1 (101s ago)   15h
vigil-api-8554454f7b-z5r9r           1/1     Running     1 (101s ago)   14h
vigil-demo-shop-c78d44bbb-j45jb      1/1     Running     1 (101s ago)   15h
vigil-frontend-ffb54777f-7x7cr       1/1     Running     2 (47s ago)    14h
vigil-frontend-ffb54777f-kb4kw       1/1     Running     2 (53s ago)    15h
vigil-notifier-68d8f9c644-h6psz      1/1     Running     1 (101s ago)   15h
vigil-postgres-0                     1/1     Running     1 (101s ago)   15h
vigil-redis-0                        1/1     Running     0              100s
vigil-worker-5bb4c8684-wqpcz         1/1     Running     0              29s    <- KEDA woke it for the queued check

$ kubectl get cronjobs -n vigil
NAME                  SCHEDULE      TIMEZONE       SUSPEND   ACTIVE   LAST SCHEDULE   AGE
errand-4714e86efbc1   */1 * * * *   Asia/Kolkata   False     1        5s              15h
errand-a34009dd1733   */5 * * * *   Asia/Kolkata   False     0        14h             15h

$ curl -s -o /dev/null -w "UI %{http_code}\n" http://vigil.localhost:8081/
UI 200

$ curl -sS -X POST -H 'Content-Type: application/json' --data-binary @plan.json http://vigil.localhost:8081/api/plan
  # plan.json = {"text":"Tell me when http://vigil-demo-shop:8000/static drops below ₹1,20,000, check every minute"}
{"spec":{"name":"vigil-demo-shop: price below ₹1,20,000","url":"http://vigil-demo-shop:8000/static",
 "kind":"price_below","threshold":120000.0,"keyword":null,"selector":null,"render":false,"every_minutes":1},
 "source":"rules","notes":[]}
```

Gotcha: put non-ASCII text (the ₹ sign) in a UTF-8 file and send it with
`--data-binary @file`. Passed directly on a Windows command line the ₹ can be
mangled by the console code page, and the planner then sees no price and falls
back to `page_changed`. `scripts/demo.ps1` already does this.

## Phase 2 - Helm chart (`charts/vigil`, release `vigil` in namespace `vigil`)

Chart layout: `Chart.yaml` (v2, chart 0.1.0, appVersion 0.1.0), `values.yaml`
(documented defaults), `values-dev.yaml`, `values-prod.yaml`, and `templates/`:
`_helpers.tpl` (labels, selectorLabels, image, securityContext, checksum helpers),
`namespace.yaml` (optional), `configmap.yaml`, `secret.yaml` (only when
`secrets.create`), `postgres.yaml`, `redis.yaml`, `api.yaml` (SA + Role + RoleBinding +
Service + Deployment), `worker.yaml` (+ KEDA ScaledObject), `notifier.yaml`,
`frontend.yaml`, `demo-shop.yaml` (dev), `llm-gateway.yaml` (prod: LiteLLM ConfigMap +
Service + Deployment), `ingress.yaml`, `networkpolicies.yaml`, `NOTES.txt`,
`tests/test-api.yaml`.

| | values-dev.yaml | values-prod.yaml |
|---|---|---|
| demo shop | on (`demo.localhost`) | off |
| worker min replicas (KEDA) | 0 | 1 |
| LLM | `fake` | `gateway` (LiteLLM, key optional) |
| MIN_INTERVAL_MINUTES | 1 | 5 |
| Secret | created by the chart, dev-only placeholder values | `secrets.create: false`, expects existing `vigil-secrets` (SealedSecret in Phase 3) |
| UI host | `vigil.localhost` | `vigil-prod.localhost` |

Design notes:
- Object names are fixed (`vigil-api`, `vigil-redis`, ...) because the app and the
  frontend's nginx have the Service names built in. One release per namespace.
- Images are set per component (`<component>.image.registry/repository/tag/pullPolicy`);
  defaults are the local `vigil-core:dev`, `vigil-worker:dev`, `vigil-frontend:dev` with
  `IfNotPresent`. Phase 4 sets `registry: ghcr.io` + `repository: gagan046-dev/...`.
- Pods that read `vigil-config` carry `checksum/config` (and `checksum/secret` when the
  chart makes the Secret), so `helm upgrade` with a changed setting rolls them.
- `helm install --create-namespace` would NOT add the Pod Security labels, so
  `scripts/phase2-up.ps1` creates and labels the namespace first.
- The Phase-1 objects cannot be adopted by Helm (selectors now also contain
  `app.kubernetes.io/instance`, and selectors are immutable), so namespace `vigil` was
  deleted before the install.
- The LiteLLM image (v1.103.2) defaults to root; tested beforehand with
  `docker run --user 10001:10001 --read-only --tmpfs /tmp` that it starts fine
  unprivileged (`/health/liveliness` -> "I'm alive!", `/health/readiness` -> healthy).
  It was NOT deployed in the cluster in Phase 2 (prod values only dry-run checked);
  its image (1.66 GB) must be imported/pulled for Phase 3.
- With `minReplicas > 0` (prod) the ScaledObject leaves out `pollingInterval` and
  `cooldownPeriod` (KEDA warns that they are irrelevant then).

### Lint and server-side dry run

```text
$ helm lint charts/vigil -f charts/vigil/values-dev.yaml     # same for values-prod.yaml, also --strict
[INFO] Chart.yaml: icon is recommended
1 chart(s) linted, 0 chart(s) failed

$ kubectl create ns vigil-dryrun
$ kubectl label ns vigil-dryrun pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/warn=restricted
$ helm template vigil charts/vigil -n vigil-dryrun -f charts/vigil/values-dev.yaml | kubectl apply --dry-run=server -f -
networkpolicy.networking.k8s.io/default-deny-ingress created (server dry run)
... (27 objects, all "created (server dry run)", no Pod Security warnings)
deployment.apps/vigil-worker created (server dry run)
scaledobject.keda.sh/vigil-worker created (server dry run)
pod/vigil-test-api created (server dry run)
$ helm template ... -f charts/vigil/values-prod.yaml | kubectl apply --dry-run=server -f -
... (27 objects incl. configmap/deployment/service vigil-llm-gateway, allow-llm-gateway; all created)
$ kubectl delete ns vigil-dryrun
```

(A dry run against the still-running Phase-1 namespace failed with 9x
"field is immutable" - the changed selectors - which is why a fresh namespace was used.)

### Install

```powershell
kubectl delete namespace vigil                     # remove the Phase-1 raw-YAML install
powershell -ExecutionPolicy Bypass -File scripts\phase2-up.ps1
#   = kubectl create namespace vigil + PSS labels
#     helm upgrade --install vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml --wait --timeout 6m
#     helm test vigil -n vigil
```

```text
Release "vigil" does not exist. Installing it now.
STATUS: deployed   REVISION: 1
TEST SUITE:     vigil-test-api
Phase:          Succeeded
pod/vigil-api-767f7fd7b6-6dqrm         1/1     Running   0          33s
pod/vigil-api-767f7fd7b6-s6sqs         1/1     Running   0          33s
pod/vigil-demo-shop-7d6b964d84-vf6d2   1/1     Running   0          33s
pod/vigil-frontend-75bb845d8b-vt8xm    1/1     Running   0          33s
pod/vigil-frontend-75bb845d8b-z4b54    1/1     Running   0          33s
pod/vigil-notifier-5556cf5fb9-6bvlx    1/1     Running   0          33s
pod/vigil-postgres-0                   1/1     Running   0          33s
pod/vigil-redis-0                      1/1     Running   0          33s
ingress.networking.k8s.io/vigil   traefik   vigil.localhost,demo.localhost
scaledobject.keda.sh/vigil-worker   ... MIN 0  MAX 5  READY True  ACTIVE False   (worker scaled to 0)
```

### Short end-to-end check

```text
$ curl.exe -s -o NUL -w "UI %{http_code}" http://vigil.localhost:8081/
UI 200
$ powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
System status: env dev, llm_mode fake, scheduler kubernetes, min_interval_minutes 1
Plan  -> kind price_below, threshold 120000.0, every_minutes 1, source rules
Dry run -> ok True, value 134990.0, condition_true False, selector span.price-now ("Found ₹1,34,990 via heuristic")
Keep watch -> errand 6989d0b7-...  schedule '*/1 * * * *'
  NAME                  SCHEDULE      TIMEZONE       SUSPEND   ACTIVE
  errand-6989d0b758ed   */1 * * * *   Asia/Kolkata   False     0
first check: baseline 134990.0
POST demo.localhost:8081/admin/price {value:114990}
NOTIFICATION: Price drop: Aurora 15 (RTX 5070) is ₹1,14,990 (target < ₹1,20,000, was ₹1,34,990)
```

Two minutes later - still exactly ONE notification (transition logic), later checks "unchanged":

```text
GET /api/inbox -> 1 item: 07:49:03 Price drop: Aurora 15 (RTX 5070) is ₹1,14,990 (...)
GET /api/log   -> 07:51:02 unchanged 114990 | 07:50:07 unchanged 114990 | 07:49:03 triggered 114990 | 07:48:24 baseline 134990
```

### Upgrade (checksum restart), rollback, history

```text
$ helm upgrade vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml --set config.hostMinGapMs=4000 --wait
--- before: checksum/config on api + notifier pods
vigil-api-767f7fd7b6-6dqrm        bcddbea9f43b...
vigil-notifier-5556cf5fb9-6bvlx   bcddbea9f43b...
--- after (new ReplicaSets, new hash; ConfigMap HOST_MIN_GAP_MS=4000)
vigil-api-7bb7c9c669-8cj8l        e62fa25efe26...
vigil-api-7bb7c9c669-9vvlx        e62fa25efe26...
vigil-notifier-6b7679fdd4-xgvsq   e62fa25efe26...

$ helm rollback vigil 1 -n vigil --wait
Rollback was a success! Happy Helming!
HOST_MIN_GAP_MS=5000; pods back on the original hash (vigil-api-767f7fd7b6-..., bcddbea9f43b...)

$ helm history vigil -n vigil
REVISION  STATUS      CHART        APP VERSION  DESCRIPTION
1         superseded  vigil-0.1.0  0.1.0        Install complete
2         superseded  vigil-0.1.0  0.1.0        Upgrade complete
3         deployed    vigil-0.1.0  0.1.0        Rollback to 1

$ helm test vigil -n vigil         # after the rollback
TEST SUITE: vigil-test-api   Phase: Succeeded
```

### Teardown (Phase 3 redeploys with Argo CD)

```text
$ helm uninstall vigil -n vigil
$ kubectl delete namespace vigil
```
The cluster and KEDA (namespace `keda`) stay running.
