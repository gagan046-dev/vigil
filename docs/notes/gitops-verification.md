# GitOps verification notes (Phases 3 and 4)

Commands that were run, with trimmed output. Date: 2026-10-21. Cluster: k3d "vigil"
(1 server + 2 agents, k3s v1.35.5), ingress on http://*.localhost:8081.

## Step A - GitHub repository

```bash
# secret scan of the whole history before going public
git log --all -p | grep -nEi 'sk-or-|ghp_|github_pat_|api_key\s*=|gho_|AKIA[0-9A-Z]{16}|BEGIN (RSA|OPENSSH|EC) PRIVATE'
#   -> only test fixtures: llm_api_key="sk-test" (unit test), llm_api_key=e("LLM_API_KEY","")
git log --all -p | grep -nEi '(password|secret|token)["'\'']?\s*[:=]\s*["'\'']?[A-Za-z0-9+/_-]{8,}'
#   -> only documented placeholders: dev-only-insecure-*, CHANGE-ME-placeholder, test-token-123

gh repo create gagan046-dev/vigil --public --source . --remote origin \
  --description "Vigil — an AI agent that keeps watch on web pages for you. Kubernetes + Helm + Argo CD learning project."
#   https://github.com/gagan046-dev/vigil
```

Pushing: Windows' Git Credential Manager on this PC holds a token for a *different*
GitHub account (push was denied with 403). Without changing any git config, every push
uses the gh login for that one command:

```bash
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main --tags
```

## Step B - Phase 3: Argo CD

### Install Argo CD (the only manual install)

```bash
helm repo add argo https://argoproj.github.io/argo-helm
helm upgrade --install argocd argo/argo-cd --version 10.9.4 -n argocd --create-namespace \
  -f gitops/bootstrap/argocd-values.yaml --wait
kubectl get pods,ingress -n argocd
#   argocd-application-controller-0 1/1 Running, argocd-repo-server 1/1, argocd-server 1/1, argocd-redis 1/1
#   ingress argocd-server  traefik  argocd.localhost
curl -s -o /dev/null -w "%{http_code}" http://argocd.localhost:8081/healthz   # 200
```

UI: http://argocd.localhost:8081, user `admin`. Password (printed by you, never stored in git):

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d
```
```powershell
# PowerShell
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}")))
```

### App of apps

```bash
kubectl apply -f gitops/bootstrap/root-app.yaml
kubectl get applications -n argocd
#   NAME             SYNC STATUS   HEALTH STATUS
#   keda             Synced        Healthy
#   root             Synced        Healthy
#   sealed-secrets   Synced        Healthy
#   vigil-dev        Synced        Healthy
```

* KEDA was adopted, not reinstalled: the keda pods kept their age (15h) and restart count.
  The old Helm release records were then removed so Helm and Argo CD never both manage KEDA:
  `kubectl delete secret -n keda -l owner=helm,name=keda` (4 secrets v1..v4 deleted).
* sealed-secrets: `https://bitnami-labs.github.io/sealed-secrets/index.yaml` returned
  **404**, so the Application takes the chart from git
  (`github.com/bitnami-labs/sealed-secrets`, path `helm/sealed-secrets`, tag `helm-v2.20.0`).
* Namespace labels come from `managedNamespaceMetadata`:
  `kubectl get ns vigil-dev --show-labels` -> `pod-security.kubernetes.io/enforce=restricted` (+ warn, audit).
* Sync waves: inside the chart ConfigMap + Secret are wave -2, Postgres + Redis wave -1,
  the rest wave 0; the prod SealedSecret is wave -3. Child apps: keda and sealed-secrets -3,
  vigil-dev / vigil-prod 0.

### Prod secret (SealedSecret)

```bash
# kubeseal v0.40.0 downloaded to the session scratchpad (NOT the repo)
kubeseal --version          # kubeseal version: 0.40.0
# random values exist only in shell variables, piped straight into kubeseal
kubectl create secret generic vigil-secrets -n vigil-prod \
  --from-literal=postgres-password="$(openssl rand -hex 24)" \
  --from-literal=internal-token="$(openssl rand -hex 32)" \
  --from-literal=llm-master-key="sk-$(openssl rand -hex 24)" \
  --dry-run=client -o yaml | kubeseal --format yaml > gitops/envs/prod/vigil-secrets.sealed.yaml
kubectl get secret vigil-secrets -n vigil-prod   # created by the controller after Argo synced
```

To add the OpenRouter key later (your job): run the same command with an extra
`--from-literal=openrouter-api-key=<your key>`, or merge just that key:

```bash
kubectl create secret generic vigil-secrets -n vigil-prod --from-literal=openrouter-api-key=<key> \
  --dry-run=client -o yaml | kubeseal --format yaml --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
git commit -am "chore: add OpenRouter key (sealed)" && git push
kubectl rollout restart deploy/vigil-llm-gateway -n vigil-prod   # env vars are read at start
```

If the cluster is ever recreated the controller gets a new key pair, and the file must be
re-sealed with the commands above.

### Prod environment

The LiteLLM image could not be pulled by the nodes at first
(`dial tcp: lookup ghcr.io: Try again` - DNS inside the k3d nodes), so it was imported:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1 -Images ghcr.io/berriai/litellm:v1.103.2
kubectl delete pod -n vigil-prod -l app.kubernetes.io/component=llm-gateway
```
```
kubectl get pods -n vigil-prod
  vigil-api x2, vigil-frontend x2, vigil-llm-gateway, vigil-notifier, vigil-postgres-0,
  vigil-redis-0, vigil-worker (min 1)                       all 1/1 Running
kubectl get applications -n argocd   ->  vigil-prod  Synced  Healthy
```

LLM gateway without an OpenRouter key -> the app falls back to rules:

```
POST http://vigil-prod.localhost:8081/api/plan {"text":"keep an eye on https://example.com/board for me"}
  -> source "rules", notes: "The AI planner was unavailable; used the rule-based parser."
gateway log: OpenrouterException {"message":"No cookie auth credentials found","code":401}
             "POST /v1/chat/completions HTTP/1.1" 401 Unauthorized
GET /api/system -> {"env":"prod","llm_mode":"gateway","llm_budget_used":1,"min_interval_minutes":5}
```

(A clear request such as "... drops below ₹1,20,000, check every 30 minutes" is planned by
the rules directly and never calls the model - attention routing.)

### Dev environment: errand + notification

```
GET  http://vigil-dev.localhost:8081/            -> 200
GET  http://demo.localhost:8081/static           -> 200
POST /api/plan   "...http://vigil-demo-shop:8000/static drops below ₹1,20,000, check every 1 minute"
     -> price_below, threshold 120000, every_minutes 1, source rules
POST /api/dryrun -> ok, value 134990, condition_true false, selector span.price-now
POST /api/errands -> 201, CronJob errand-c6660d3c02d4  */1 * * * *  Asia/Kolkata
POST http://demo.localhost:8081/admin/price {"value":114990}
GET  /api/inbox  -> "Price drop: Aurora 15 (RTX 5070) is ₹1,14,990 (target < ₹1,20,000, was ₹1,34,990)"
GET  /api/log    -> baseline 134990 -> unchanged (cheap path) -> triggered 114990 (via learned selector)
```

The CronJobs the api creates at runtime are not in git and carry no Argo CD tracking
annotation, so Argo CD neither shows them as drift nor prunes them.

### Self-heal demo

```
13:46:21  kubectl scale deploy vigil-frontend -n vigil-dev --replicas=5
13:46:22  spec.replicas=5
13:46:26  spec.replicas=2        <- Argo CD (selfHeal) put it back within ~5 s
```

### Git-driven change + rollback by git revert

```
commit 814c072 "demo(phase-3): scale dev frontend to 3 replicas via git" (values-dev.yaml: frontend.replicas: 3)
13:46:41 push -> 13:47:40 vigil-frontend spec.replicas=3, app revision 814c0724...
commit c9429ed  git revert 814c072
13:51:10 push -> 13:52:16 vigil-frontend spec.replicas=2
```

(Argo CD polls git every 60 s - `timeout.reconciliation: 60s` in argocd-values.yaml -
so a push shows up within about a minute. A GitHub webhook would make it instant, but
this cluster is not reachable from the internet.)

## Step C - Phase 4: CI -> GitOps

### Action pins (looked up with gh api, releases at least 2 weeks old on 2026-10-21)

```
gh api repos/<owner>/<action>/git/ref/tags/<tag>   (annotated tags dereferenced via git/tags/<sha>)
actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1            # v7.0.1  (2026-07-20)
actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97        # v7.0.0  (2026-07-20)
azure/setup-helm@9bc31f4ebc9c6b171d7bfbaa5d006ae7abdb4310            # v5.0.1  (2026-06-23)
docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069  # v4.4.1  (2026-09-16)
docker/login-action@dbcb813823bdd20940b903addbd779551569679f         # v4.6.0  (2026-07-29)
docker/metadata-action@dc802804100637a589fabce1cb79ff13a1411302      # v6.2.0  (2026-07-02)
docker/build-push-action@c3c9e263c25d99ce0380d002d59b67737d91b0dc    # v7.4.0  (2026-09-15)
helm v4.3.0 (2026-09-09; v4.3.1 is too new), kubeconform v0.8.0 (checksum verified)
```

### Local pre-check with the pinned tool versions (scratch venv, not in the repo)

```
ruff check .           -> All checks passed!
ruff format --check .  -> 30 files already formatted
pytest                 -> 190 passed
helm template + generated values-dev-images.yaml -> api/notifier/demo-shop/migrate use
  ghcr.io/gagan046-dev/vigil-core:sha-..., worker vigil-worker, frontend vigil-frontend
```

### Repository setting for the promote workflow

```bash
gh api -X PUT repos/gagan046-dev/vigil/actions/permissions/workflow \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=true
#   {"default_workflow_permissions":"read","can_approve_pull_request_reviews":true}
```
(The default token stays read-only; this only lets a workflow that asks for
`pull-requests: write` open a PR.)

### First CI run (push of e4cf0da) - green on the first attempt

```
gh run watch 39569238368 --exit-status
  ✓ helm lint + template        7s
  ✓ lint + test                26s
  ✓ build + push vigil-worker  4m0s
  ✓ build + push vigil-core     50s
  ✓ build + push vigil-frontend 20s
  ✓ bump dev image tag           7s
bump-dev log:
  vigil-core:sha-e4cf0da anonymous pull -> HTTP 200   (same for worker, frontend)
  [main 1ddfd37] chore(dev): deploy images sha-e4cf0da [skip ci]
gh run list  -> no run was started for 1ddfd37 (GITHUB_TOKEN push + [skip ci]: no loop)
```

### GHCR package visibility

```
gh api "/users/gagan046-dev/packages?package_type=container" --jq '.[]|[.name,.visibility]|@tsv'
  vigil-frontend  public
  vigil-core      public
  vigil-worker    public
```

### Argo CD deploys the CI-built images to vigil-dev

First attempt: every new pod went to `ImagePullBackOff` with
`dial tcp: lookup ghcr.io: Try again`. Cause: inside the k3d nodes, Docker Desktop's DNS
(192.168.65.254) answers A queries for ghcr.io but TIMES OUT on AAAA queries
(`nslookup -type=aaaa ghcr.io` -> "connection timed out"), and containerd's resolver gives
up. 1.1.1.1 answers at once. Fix (now also in scripts/phase3-up.ps1):

```bash
for n in k3d-vigil-server-0 k3d-vigil-agent-0 k3d-vigil-agent-1; do
  docker exec $n sh -c 'grep -q "^nameserver 1.1.1.1" /etc/resolv.conf || echo "nameserver 1.1.1.1" >> /etc/resolv.conf'
done
docker exec k3d-vigil-agent-0 crictl pull ghcr.io/gagan046-dev/vigil-frontend:sha-e4cf0da   # ok in 4 s
kubectl delete pod -n vigil-dev <the ImagePullBackOff pods>
```
```
kubectl get deploy -n vigil-dev -o wide
  vigil-api        2/2  ghcr.io/gagan046-dev/vigil-core:sha-e4cf0da
  vigil-demo-shop  1/1  ghcr.io/gagan046-dev/vigil-core:sha-e4cf0da
  vigil-frontend   2/2  ghcr.io/gagan046-dev/vigil-frontend:sha-e4cf0da
  vigil-notifier   1/1  ghcr.io/gagan046-dev/vigil-core:sha-e4cf0da
  vigil-worker     1/1  ghcr.io/gagan046-dev/vigil-worker:sha-e4cf0da
kubectl get application vigil-dev -n argocd -> Synced Healthy
curl http://vigil-dev.localhost:8081/ -> 200; the existing watch kept running on the new images
```

### Promotion to prod (pull request)

```
gh workflow run promote.yaml          # no tag given -> takes the tag dev runs (sha-e4cf0da)
gh run watch 39571639300 --exit-status   -> success
gh pr list -> #1 "Promote sha-e4cf0da to prod"  promote/prod-sha-e4cf0da  OPEN
  diff: charts/vigil/values-prod-images.yaml {} -> ghcr.io/gagan046-dev/*:sha-e4cf0da
```
The PR is left open on purpose: merging it is the human approval that deploys to prod
(Argo CD then syncs vigil-prod from main). The CI run for this bot-opened PR shows
`action_required`: GitHub waits for a human to click "Approve and run workflows" on the PR
before running CI for a PR created by GITHUB_TOKEN.

### Second push, fully automatic (after the DNS fix)

```
push 5f612bf -> CI run 39571768562: all 6 jobs green (worker build 23 s thanks to the GHA cache)
bump-dev -> ed991bf "chore(dev): deploy images sha-5f612bf [skip ci]"
Argo CD -> every vigil-dev Deployment on ghcr.io/gagan046-dev/*:sha-5f612bf, Synced/Healthy,
           no manual step
gh run list -> no run for 1ddfd37 or ed991bf (no CI loop)
kubectl get applications -n argocd -> keda, root, sealed-secrets, vigil-dev, vigil-prod all Synced/Healthy
```
