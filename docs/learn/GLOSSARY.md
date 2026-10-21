# Glossary

Every technical word used in Vigil and its learning chapters, A to Z. Each entry has a
one-line beginner definition and **where it appears in Vigil**, so you can go and look
at the real thing.

Jump to: [A](#a) · [B](#b) · [C](#c) · [D](#d) · [E](#e) · [F](#f) · [G](#g) · [H](#h) ·
[I](#i) · [J](#j) · [K](#k) · [L](#l) · [M](#m) · [N](#n) · [O](#o) · [P](#p) · [R](#r) ·
[S](#s) · [T](#t) · [U](#u) · [V](#v) · [W](#w) · [Y](#y)

## A

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Admin password (Argo CD)** | The password of Argo CD's built-in `admin` user, generated at install time and stored in a Secret. | Secret `argocd-initial-admin-secret` in namespace `argocd`; read it with the command in `COMMANDS.md` section 5 |
| **Agent (k3d/k3s)** | A worker node: a machine that runs your pods but not the control plane. | `agents: 2` in `cluster/k3d-config.yaml` -> `k3d-vigil-agent-0`, `k3d-vigil-agent-1` |
| **Annotation** | A free-text note attached to a Kubernetes object; tools read it, selectors ignore it. | `checksum/config` on pods, `argocd.argoproj.io/sync-wave`, `helm.sh/hook` |
| **API server** | The front door of Kubernetes: every `kubectl` command and every controller talks to it over HTTPS. | `https://127.0.0.1:6550` (`kubeAPI` in `cluster/k3d-config.yaml`) |
| **apiVersion** | The first line of every manifest: which API group and version defines this kind of object. | `apps/v1` (Deployment), `batch/v1` (CronJob), `keda.sh/v1alpha1`, `argoproj.io/v1alpha1` |
| **App of apps** | An Argo CD Application whose only job is to create other Applications from a folder. | `gitops/bootstrap/root-app.yaml` -> `gitops/apps/` |
| **appVersion** | The version of the application a Helm chart deploys (not the chart's own version). | `appVersion: "0.1.0"` in `charts/vigil/Chart.yaml` = `VERSION` in `app/vigil/config.py` |
| **Application (Argo CD)** | A custom resource that says "keep this path of this git repo deployed into this namespace". | `gitops/apps/keda.yaml`, `sealed-secrets.yaml`, `vigil-dev.yaml`, `vigil-prod.yaml` |
| **Argo CD** | A GitOps controller running in the cluster that keeps the cluster equal to what is in git. | Namespace `argocd`, installed by `scripts/phase3-up.ps1` (chart argo-cd 10.9.4, v3.5.3) |
| **Attention routing** | Vigil's rule: try the cheap deterministic path first and call the LLM only when it is really needed. | `app/vigil/llm.py`, `app/vigil/planner.py` |

## B

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Baseline** | The first value a watch records; later checks are compared against it. | `/api/log` shows `baseline 134990` for the demo watch |
| **base64** | A way to write bytes as plain text. Kubernetes stores Secret values in base64 - it is encoding, **not** encryption. | `kubectl get secret ... -o jsonpath="{.data.password}"` must be decoded |
| **BLPOP** | A Redis command: take the first item of a list, waiting until one arrives. | The worker pops jobs from `vigil:checks` (`app/vigil/queue.py`) |
| **Bootstrap** | The one manual first step that lets automation take over. | `gitops/bootstrap/`: install Argo CD with Helm, apply `root-app.yaml` |

## C

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Capabilities (Linux)** | Fine-grained root powers a process can have; dropping them all makes a container much safer. | `capabilities: drop: ["ALL"]` in `vigil.containerSecurity` (`_helpers.tpl`) |
| **Chart (Helm)** | A folder of templates plus default values that Helm turns into Kubernetes YAML. | `charts/vigil/` |
| **Chart.yaml** | The chart's identity card: name, chart version, app version, minimum Kubernetes version. | `charts/vigil/Chart.yaml` |
| **Checksum annotation** | A hash of the config put on the pod template, so a config change changes the pod and triggers a restart. | `vigil.configChecksums` in `_helpers.tpl`; demonstrated with `helm upgrade --set config.hostMinGapMs=4000` |
| **CI (Continuous Integration)** | Automatically linting, testing and building every push. | `.github/workflows/ci.yaml` jobs `lint-test` and `helm` |
| **CD (Continuous Delivery/Deployment)** | Automatically getting a tested build into an environment. | Job `bump-dev` + Argo CD (dev); `promote.yaml` PR (prod) |
| **ClusterIP** | The default Service type: a stable virtual IP reachable only inside the cluster. | `type: ClusterIP` in `templates/api.yaml` |
| **Cluster** | A group of machines (nodes) run together by Kubernetes as one big computer. | k3d cluster `vigil` (`cluster/k3d-config.yaml`) |
| **ConfigMap** | A Kubernetes object holding non-secret settings as key/value pairs. | `vigil-config` (`k8s/01-configmap.yaml`, `templates/configmap.yaml`) |
| **Container** | A packaged process with its own filesystem, made from an image. | Every pod in Vigil runs one or more containers |
| **containerd / ctr / crictl** | The container runtime inside each k3s node, and its command-line tools. | `scripts/import-images.ps1` runs `ctr images import`; `crictl pull` tests GHCR |
| **Control plane** | The "brain" of the cluster: API server, scheduler, controllers, etcd. | Runs on `k3d-vigil-server-0` |
| **Controller** | A program that loops forever: compare desired state with actual state, fix the difference. | Deployment controller, KEDA operator, Argo CD application controller, sealed-secrets controller |
| **Cooldown period** | How long KEDA waits with an empty queue before scaling back to zero. | `cooldownPeriod: 60` in `values.yaml` (`worker.autoscaling`) |
| **CRD (CustomResourceDefinition)** | Adds a new kind of object to Kubernetes. | `ScaledObject` (KEDA), `Application` (Argo CD), `SealedSecret` |
| **CronJob** | Runs a Job on a schedule written in cron syntax. | One per watch, created at runtime by the api (`app/vigil/scheduler.py`, name `errand-<id>`) |
| **Cron expression** | Five fields (minute hour day month weekday) describing a schedule. | `*/1 * * * *` (every minute), `*/5 * * * *` |

## D

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Default deny** | A NetworkPolicy that blocks all incoming traffic unless another policy allows it. | `default-deny-ingress` in `templates/networkpolicies.yaml` |
| **Deployment** | Keeps N identical pods running and replaces them gradually on updates. | `vigil-api`, `vigil-worker`, `vigil-notifier`, `vigil-frontend`, `vigil-demo-shop` |
| **Desired state** | What you declared you want (in YAML / git); Kubernetes works to make reality match it. | Everything in `k8s/`, `charts/`, `gitops/` |
| **Dockerfile** | The recipe for building a container image. | `app/Dockerfile`, `app/Dockerfile.worker`, `frontend/Dockerfile` |
| **Drift** | When the live cluster differs from git. Argo CD shows it as OutOfSync. | Self-heal demo: `kubectl scale ... --replicas=5` was drift |
| **Dry run** | Doing everything except the real change, to check it. Kubernetes: `--dry-run=server`; Vigil: a one-off check. | `kubectl apply --dry-run=server`, `POST /api/dryrun` |

## E

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Egress** | Traffic going **out** of a pod. | `worker-egress` NetworkPolicy blocks private ranges for the worker |
| **emptyDir** | A scratch volume that lives as long as the pod; can be RAM-backed. | `/tmp` and `/dev/shm` (Chromium) in `templates/worker.yaml` |
| **Endpoint** | An address (pod IP + port) behind a Service. | `kubectl get endpoints vigil-api -n vigil-dev` |
| **envFrom** | Load every key of a ConfigMap or Secret as environment variables. | `envFrom: configMapRef: vigil-config` in every Vigil pod |
| **Errand** | Vigil's internal name for a watch (a standing order to check a page). | Table/API `/api/errands`, CronJob `errand-<id>` |
| **etcd** | The key-value database where Kubernetes stores all objects. | Inside the k3s server (k3s uses an embedded datastore) |
| **Events** | Short messages Kubernetes records about what happened to objects (scheduled, pulled, failed). | `kubectl get events -n vigil-dev --sort-by=.lastTimestamp` |

## F

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **FastAPI** | A Python web framework for building HTTP APIs. | `app/vigil/api.py`, `app/vigil/demo_shop.py` |
| **Finalizer** | A marker that makes Kubernetes run cleanup before an object is really deleted. | `resources-finalizer.argocd.argoproj.io` on every Application |

## G

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **GHCR** | GitHub Container Registry: stores container images next to the repo. | `ghcr.io/gagan046-dev/vigil-core`, `vigil-worker`, `vigil-frontend` |
| **GitHub Actions** | GitHub's built-in CI/CD: YAML workflows run on GitHub's machines. | `.github/workflows/ci.yaml`, `promote.yaml` |
| **GitOps** | Git is the single source of truth; an agent in the cluster pulls changes from git and applies them. | Phase 3: Argo CD + `gitops/` |
| **GITHUB_TOKEN** | A short-lived token GitHub gives each workflow run; its rights are set by `permissions:`. | `contents: write` only in job `bump-dev` |

## H

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Health status (Argo CD)** | Whether the deployed objects actually work (Healthy, Progressing, Degraded). | `kubectl get applications -n argocd` column HEALTH STATUS |
| **Helm** | The package manager for Kubernetes: renders templates with values and tracks releases. | `charts/vigil`, Phase 2, `scripts/phase2-up.ps1` |
| **Helm hook** | A template annotated to run at a special moment (e.g. `helm test`). | `"helm.sh/hook": test` in `templates/tests/test-api.yaml` |
| **helm lint** | Checks a chart for mistakes. | CI job `helm` (`--strict`) |
| **helm rollback** | Returns a release to an earlier revision. | `helm rollback vigil 1 -n vigil` in `docs/notes/platform-verification.md` |
| **helm template** | Prints the rendered YAML without installing. | CI pipes it into kubeconform |
| **helm test** | Runs the chart's test pods against an installed release. | Pod `vigil-test-api` curls `/readyz` |
| **_helpers.tpl** | A chart file of reusable named templates (labels, image name, security settings). | `charts/vigil/templates/_helpers.tpl` |
| **HPA (HorizontalPodAutoscaler)** | Kubernetes' built-in autoscaler that changes a Deployment's replica count. | KEDA creates `keda-hpa-vigil-worker` |

## I

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Idempotent** | Running it twice has the same effect as once. | `kubectl apply`, `helm upgrade --install`, `vigil.migrate` |
| **ignoreDifferences** | Tells Argo CD to ignore fields that something else changes at runtime. | `caBundle` fields in `gitops/apps/keda.yaml` |
| **Image** | A read-only package (filesystem + start command) that containers are created from. | `vigil-core`, `vigil-worker`, `vigil-frontend`, `postgres:17.11-alpine`, ... |
| **Image tag** | The label after `:` that names one version of an image. | `:dev` (local), `:sha-5f612bf` (CI) |
| **imagePullPolicy** | When the node downloads the image: `Always`, `IfNotPresent`, `Never`. | `IfNotPresent` everywhere (`values.yaml`) |
| **ImagePullBackOff** | Pod status: the node failed to download the image and is waiting to retry. | The `lookup ghcr.io: Try again` DNS problem in Phase 4 |
| **Ingress** | Rules that route outside HTTP traffic by host name / path to Services. | `templates/ingress.yaml` (`vigil-dev.localhost`, `demo.localhost`) |
| **Ingress controller** | The proxy that implements Ingress rules. | Traefik (built into k3s), reached on `127.0.0.1:8081` |
| **initContainer** | A container that must finish successfully before the main containers start. | `migrate` in `templates/api.yaml` creates the database tables |

## J

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Job** | Runs a pod until it completes successfully (once, not forever). | Each CronJob tick creates a Job whose curl pod triggers a check |
| **jsonpath** | A way to pick one field out of `kubectl` output. | `-o jsonpath="{.data.password}"` |

## K

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **k3d** | Runs k3s clusters inside Docker containers - a real cluster on one PC. | `cluster/k3d-config.yaml`, `k3d cluster create` |
| **k3s** | A small, complete Kubernetes distribution by Rancher. | `rancher/k3s:v1.35.5-k3s1` |
| **KEDA** | Kubernetes Event-Driven Autoscaling: scales workloads on external signals, including to zero. | `gitops/apps/keda.yaml`, ScaledObject in `templates/worker.yaml` |
| **kind** | The type of a Kubernetes object (Pod, Service, Deployment...). | Second line of every manifest |
| **kubeconfig** | The file telling `kubectl` which cluster to talk to and how to log in. | `~/.kube/config`, context `k3d-vigil` |
| **kubeconform** | Validates YAML against Kubernetes' schemas without a cluster. | CI job `helm` |
| **kubectl** | The command-line tool for the Kubernetes API. | Every script in `scripts/` |
| **kubelet** | The agent on each node that starts and watches the node's containers. | Runs inside each k3d node container |
| **kubeseal** | The CLI that encrypts a Secret into a SealedSecret using the controller's public key. | Used to make `gitops/envs/prod/vigil-secrets.sealed.yaml` |
| **Kubernetes** | A system that runs containers across machines and keeps them in the state you declare. | The whole project |

## L

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Label** | A key/value tag on an object; selectors find objects by labels. | `app.kubernetes.io/component: api`, `vigil.dev/errand-id` |
| **Limits** | The most CPU/RAM a container may use; over the memory limit it is killed (OOMKilled). | `resources.limits` in `values.yaml` |
| **LiteLLM** | An LLM gateway with an OpenAI-compatible API that routes to many providers, with fallbacks. | `templates/llm-gateway.yaml` (prod), image `ghcr.io/berriai/litellm:v1.103.2` |
| **Liveness probe** | Kubernetes asks "are you alive?"; failing it restarts the container. | `/healthz` on the api |
| **LLM** | Large language model - the "AI" that can read messy text. | Planning and extraction fallbacks; `config.llmMode` |
| **local-path** | k3s' built-in storage provisioner: volumes are folders on the node's disk. | `storageClassName: local-path` for postgres and redis |

## M

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **managedNamespaceMetadata** | Labels/annotations Argo CD puts on a namespace it creates. | Pod Security labels in `gitops/apps/vigil-dev.yaml` and `vigil-prod.yaml` |
| **Manifest** | A YAML (or JSON) file describing Kubernetes objects. | `k8s/*.yaml` |
| **Matrix (CI)** | One job definition run several times with different inputs. | `build-push` builds 3 images |
| **metrics-server** | Collects CPU/RAM usage for `kubectl top` and the HPA. | Bundled with k3s |
| **Multi-source Application** | An Argo CD Application built from several git paths at once. | `gitops/apps/vigil-prod.yaml` (`sources:` chart + SealedSecret) |

## N

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Namespace** | A folder inside the cluster that groups objects and scopes names and permissions. | `vigil` (Phase 1-2), `vigil-dev`, `vigil-prod`, `argocd`, `keda` |
| **NetworkPolicy** | Firewall rules for pods: which pods may talk to which. | `k8s/40-networkpolicies.yaml`, `templates/networkpolicies.yaml` |
| **nindent** | Helm function: new line + indent N spaces, to place included YAML at the right depth. | `{{- include "vigil.labels" ... \| nindent 4 }}` |
| **Node** | One machine (here: one Docker container) in the cluster. | `k3d-vigil-server-0`, `k3d-vigil-agent-0/1` |
| **NOTES.txt** | Text Helm prints after install/upgrade. | `charts/vigil/templates/NOTES.txt` |
| **ntfy** | A free push-notification service: publish to a topic URL, subscribe on your phone. | `config.ntfyUrl` -> `NTFY_URL`, sent by `app/vigil/notifier.py` |

## O

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **OOMKilled** | The container used more memory than its limit and was killed. | Why the worker's limit is 1536Mi (Chromium is hungry) |
| **OpenRouter** | A service that gives one API for many LLMs, including free models. | `primaryModel` / `fallbackModel` in `values.yaml` |
| **OutOfSync** | Argo CD status: what is in the cluster is not what git says. | `kubectl get applications -n argocd` |

## P

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Playwright** | A library that drives a real headless browser (Chromium) from code. | `vigil-worker` image, renders JavaScript-heavy pages |
| **Pod** | The smallest thing Kubernetes runs: one or more containers sharing network and volumes. | `kubectl get pods -n vigil-dev` |
| **Pod Security Standards (PSS)** | Built-in profiles (`privileged`, `baseline`, `restricted`) enforced by namespace labels. | `pod-security.kubernetes.io/enforce: restricted` on every Vigil namespace |
| **port-forward** | Tunnel a local port on your PC to a pod or Service in the cluster. | `kubectl port-forward svc/vigil-api -n vigil-dev 18000:8000` |
| **Prometheus metrics** | Numbers an app exposes at `/metrics` in a standard text format. | `vigil_checks_total`, `vigil_llm_calls_total` (`app/vigil/metrics.py`) |
| **Promotion** | Moving a tested version from one environment to the next. | `promote.yaml` opens a PR changing `values-prod-images.yaml` |
| **Prune** | Argo CD deletes objects that were removed from git. | `prune: true` (dev), `prune: false` (prod) |
| **Pull request (PR)** | A proposed change on GitHub that someone reviews and merges. | PR #1 "Promote sha-e4cf0da to prod" |
| **PVC (PersistentVolumeClaim)** | A request for durable storage that outlives pods. | `volumeClaimTemplates` in `templates/postgres.yaml`, `redis.yaml` |

## R

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **RBAC** | Role-Based Access Control: who may do which verbs on which resources. | ServiceAccount `vigil-api` + Role/RoleBinding `vigil-api-cronjobs` |
| **readOnlyRootFilesystem** | The container cannot write to its own image files; only to mounted volumes. | `vigil.containerSecurity` in `_helpers.tpl` |
| **Readiness probe** | "Can you take traffic now?"; failing it removes the pod from the Service. | `/readyz` on the api (database + redis + schema) |
| **Reconciliation loop** | The controller pattern: observe, compare to desired, act, repeat. | Kubernetes controllers; Argo CD every 60 s (`timeout.reconciliation`) |
| **Redis** | An in-memory data store; Vigil uses its lists as a job queue. | `vigil-redis`, lists `vigil:checks`, `vigil:notify` |
| **Registry** | A server that stores and serves container images. | ghcr.io, Docker Hub, mcr.microsoft.com |
| **Release (Helm)** | One installed copy of a chart, with numbered revisions. | Release `vigil` (`helm history vigil -n vigil`) |
| **Replica** | One copy of a pod in a Deployment/StatefulSet. | `api.replicas: 2`, `frontend.replicas: 2` |
| **ReplicaSet** | The object a Deployment uses to keep exactly N pods of one version. | New ReplicaSet after each config change (`vigil-api-7bb7c9c669`) |
| **Requests** | The CPU/RAM a container is guaranteed; the scheduler uses them to place pods. | `resources.requests` in `values.yaml` |
| **Revert (git)** | A new commit that undoes an earlier one - the GitOps way to roll back. | `c9429ed` reverts `814c072` |
| **Role / RoleBinding** | A Role lists allowed actions in one namespace; a RoleBinding gives it to an identity. | `templates/api.yaml` |
| **Rolling update** | Replace pods a few at a time so the app stays up. | `maxUnavailable: 0, maxSurge: 1` on the api |

## S

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **ScaledObject** | KEDA's object: "scale this Deployment on this trigger between min and max". | `templates/worker.yaml` (Redis list `vigil:checks`) |
| **Scale to zero** | Running no pods at all when there is no work. | Dev worker `minReplicas: 0` (`values-dev.yaml`) |
| **Scheduler (Kubernetes)** | Picks a node for each new pod. | Part of the k3s control plane |
| **SealedSecret** | An encrypted Secret that is safe to commit; only the in-cluster controller can decrypt it. | `gitops/envs/prod/vigil-secrets.sealed.yaml` |
| **Secret** | A Kubernetes object for sensitive values (passwords, tokens). | `vigil-secrets` (keys `postgres-password`, `internal-token`, ...) |
| **secretKeyRef** | Read one key of a Secret into an environment variable; `optional: true` lets the pod start without it. | `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN` (`_helpers.tpl`, `notifier.yaml`) |
| **securityContext** | Pod/container settings for user ID, privileges and filesystem access. | `vigil.podSecurity`, `vigil.containerSecurity` in `_helpers.tpl` |
| **Selector** | A label query that picks objects (a Service's pods, a Deployment's pods). | `vigil.selectorLabels` in `_helpers.tpl` |
| **Self-heal** | Argo CD reverts manual changes in the cluster back to git. | `selfHeal: true`; demo: replicas 5 -> 2 in ~5 s |
| **Server (k3d/k3s)** | The node that runs the control plane. | `servers: 1` -> `k3d-vigil-server-0` |
| **Server-side apply** | The API server merges the YAML itself; needed for very large objects like CRDs. | `ServerSideApply=true` in `gitops/apps/keda.yaml` |
| **Service** | A stable name + virtual IP that load-balances to matching pods. | `vigil-api:8000`, `vigil-redis:6379`, `vigil-postgres:5432` |
| **Service DNS name** | Every Service gets `<name>.<namespace>.svc.cluster.local`, or just `<name>` inside its namespace. | `vigil-redis.vigil-dev.svc.cluster.local:6379` in the ScaledObject |
| **ServiceAccount** | An identity for pods to use when calling the Kubernetes API. | `vigil-api` |
| **SHA pinning** | Referring to an action/image by its unchangeable hash instead of a movable tag. | Every `uses:` in `.github/workflows/*.yaml` |
| **SSRF** | Server-Side Request Forgery: tricking a server into fetching internal addresses. | Vigil's SSRF guard + `worker-egress` policy; `allowedInternalHosts` |
| **StatefulSet** | Like a Deployment but with stable pod names and one volume per pod - for databases. | `vigil-postgres-0`, `vigil-redis-0` |
| **Sync (Argo CD)** | Applying what is in git to the cluster. | `syncPolicy.automated` in every Application |
| **Sync wave** | A number that orders what Argo CD applies first (lower first). | ConfigMap/Secret `-2`, Postgres/Redis `-1`, apps `0`; SealedSecret `-3` |

## T

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Template (Helm)** | A YAML file with `{{ }}` placeholders that Helm fills from values. | `charts/vigil/templates/*.yaml` |
| **terminationGracePeriodSeconds** | How long a pod gets to finish work after being asked to stop. | 90 s for the worker |
| **timeZone (CronJob)** | The time zone a CronJob schedule is read in. | `Asia/Kolkata` (`config.scheduleTimezone`) |
| **topologySpreadConstraints** | Ask the scheduler to spread replicas across nodes. | `templates/api.yaml` |
| **Traefik** | The reverse proxy/ingress controller built into k3s. | `ingress.className: traefik` |
| **Transition (fire on)** | Notify only when a condition changes from false to true, not on every check. | `app/vigil/evaluate.py`; one notification in the demo |

## U

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Upgrade (Helm)** | Install a new revision of a release with changed chart or values. | `helm upgrade vigil charts/vigil ...` |

## V

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Values (Helm)** | The settings a chart's templates read; later `-f` files override earlier ones. | `values.yaml` < `values-dev.yaml` < `values-dev-images.yaml` |
| **valuesObject** | Values written directly inside an Argo CD Application. | `tests.enabled: false` in `gitops/apps/vigil-dev.yaml` |
| **Volume / volumeMount** | Storage attached to a pod, and where it appears inside a container. | `/tmp`, `/dev/shm`, postgres `data` |

## W

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **Webhook** | An HTTP call one system makes to another when something happens. | Not used: Argo CD polls GitHub because the cluster is not reachable from the internet |
| **Worker** | The process that takes jobs from a queue and does the work. | `vigil-worker` (`app/vigil/worker.py`) |
| **workflow_dispatch** | A GitHub Actions trigger to start a workflow by hand. | `promote.yaml` (`gh workflow run promote.yaml`) |

## Y

| Term | Beginner definition | Where in Vigil |
|---|---|---|
| **YAML** | An indentation-based text format; Kubernetes, Helm, Argo CD and GitHub Actions are all configured with it. | Almost every file outside `app/` and `frontend/` |
