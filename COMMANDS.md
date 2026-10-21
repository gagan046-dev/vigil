# Vigil command cheat sheet

Copy-paste commands for every phase, grouped by job. Run them **from the repo root**
(`C:\Users\gagan\OneDrive\Desktop\AIOPs\vigil`).

- `PS>` blocks are **PowerShell**. `$` blocks are **Git Bash**. Most `kubectl`, `helm`,
  `k3d`, `git` and `gh` lines work the same in both.
- In Windows PowerShell 5.1, `curl` is an alias for `Invoke-WebRequest`. Always type
  **`curl.exe`**.
- Namespaces: Phase 1 and 2 use `vigil`; from Phase 3 on Argo CD runs `vigil-dev` and
  `vigil-prod`. Replace `-n vigil-dev` with the one you use.
- Host ports 3000, 8000, 8080, 3001, 9090, 9093 and 5000 belong to other projects on this
  PC. Vigil uses **8081** (ingress) and **6550** (Kube API). For `port-forward` pick
  something free like 18000.

If `k3d` or `helm` is not found in Git Bash:

```bash
export PATH="$PATH:/c/Users/gagan/AppData/Local/Microsoft/WinGet/Packages/k3d.k3d_Microsoft.Winget.Source_8wekyb3d8bbwe:/c/Users/gagan/AppData/Local/Microsoft/WinGet/Packages/Helm.Helm_Microsoft.Winget.Source_8wekyb3d8bbwe/windows-amd64"
```

## Contents

1. [Cluster lifecycle](#1-cluster-lifecycle)
2. [Images: build and import](#2-images-build-and-import)
3. [Phase 1: raw YAML](#3-phase-1-raw-yaml)
4. [Phase 2: Helm](#4-phase-2-helm)
5. [Phase 3: Argo CD](#5-phase-3-argo-cd)
6. [Phase 4: CI/CD](#6-phase-4-cicd)
7. [Using Vigil, the demo shop, price compare and SearXNG](#7-using-vigil-and-the-demo-shop)
8. [Debugging](#8-debugging)
9. [Watching KEDA scale](#9-watching-keda-scale)
10. [Cleanup and freeing RAM](#10-cleanup-and-freeing-ram)
11. [Your actions: secrets and notifications](#11-your-actions-secrets-and-notifications)
12. [Git: tags and history](#12-git-tags-and-history)

---

## 1. Cluster lifecycle

```powershell
# Create the cluster: 1 server + 2 agents, k3s v1.35.5, ingress on 127.0.0.1:8081, API on 127.0.0.1:6550
k3d cluster create --config cluster/k3d-config.yaml

# Is it there and running?
k3d cluster list
kubectl get nodes                      # 3 nodes, all Ready
kubectl config current-context         # should print k3d-vigil

# Stop it (frees CPU and RAM, keeps everything: volumes, watches, Argo CD)
k3d cluster stop vigil

# Start it again (pods recover by themselves in about 2 minutes)
k3d cluster start vigil

# Delete it completely (all data gone; a new cluster also gets a NEW sealed-secrets key)
k3d cluster delete vigil
```

After the nodes are **recreated** (not after a plain stop/start), re-apply the node DNS
fix so the nodes can pull from `ghcr.io` (Docker Desktop's DNS times out on AAAA queries):

```bash
for n in k3d-vigil-server-0 k3d-vigil-agent-0 k3d-vigil-agent-1; do
  docker exec $n sh -c 'grep -q "^nameserver 1.1.1.1" /etc/resolv.conf || echo "nameserver 1.1.1.1" >> /etc/resolv.conf'
done
```

(`scripts\phase3-up.ps1` does this for you.)

## 2. Images: build and import

```powershell
# Build Vigil's three images locally (tag :dev is what the chart uses by default)
docker build -t vigil-core:dev app
docker build -t vigil-worker:dev -f app/Dockerfile.worker app
docker build -t vigil-frontend:dev frontend

# Copy local images into every k3d node (they cannot pull images that only exist on this PC)
powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1

# Import just one image (e.g. after rebuilding it, or the LiteLLM image for prod)
powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1 -Images vigil-core:dev
powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1 -Images ghcr.io/berriai/litellm:v1.103.2

# Which images does a node have?
docker exec k3d-vigil-agent-0 crictl images | Select-String vigil

# Test that a node can pull from GHCR
docker exec k3d-vigil-agent-0 crictl pull ghcr.io/gagan046-dev/vigil-frontend:sha-5f612bf
```

Why a script and not `k3d image import`? With Docker Desktop's containerd image store
`k3d image import` can report success while importing nothing; the script streams
`docker save` into `ctr images import` on each node instead.

## 3. Phase 1: raw YAML

Namespace `vigil`, hosts `vigil.localhost` and `demo.localhost`.

```powershell
# KEDA must exist first (the worker's ScaledObject is a KEDA object)
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda --version 2.21.0 -n keda --create-namespace

# Apply k8s/00..40 in dependency order, creating a random dev Secret, waiting at each step
powershell -ExecutionPolicy Bypass -File scripts\phase1-up.ps1

# Or by hand, one file at a time
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
powershell -ExecutionPolicy Bypass -File scripts\create-dev-secret.ps1      # never prints the values
kubectl apply -f k8s/10-postgres.yaml -f k8s/11-redis.yaml
kubectl rollout status statefulset/vigil-postgres -n vigil
kubectl apply -f k8s/20-api.yaml
kubectl rollout status deployment/vigil-api -n vigil

# Everything at once
kubectl get pods,svc,ingress,scaledobject -n vigil

# End-to-end demo (defaults match Phase 1)
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1

# Remove Phase 1 (needed before Phase 2: Helm cannot adopt these objects)
kubectl delete namespace vigil
```

## 4. Phase 2: Helm

Release `vigil` in namespace `vigil`. (Today `values-dev.yaml` uses host
`vigil-dev.localhost` - it was changed in Phase 3 so dev and prod can share the cluster.)

```powershell
# Check the chart
helm lint charts/vigil -f charts/vigil/values-dev.yaml --strict
helm lint charts/vigil -f charts/vigil/values-prod.yaml --strict

# See the YAML Helm would produce (nothing is sent to the cluster)
helm template vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml | Out-File -Encoding utf8 $env:TEMP\vigil-dev.yaml

# Ask the API server to validate it without creating anything
helm template vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml | kubectl apply --dry-run=server -f -

# Install or upgrade: labels the namespace for Pod Security, helm upgrade --install --wait, helm test
powershell -ExecutionPolicy Bypass -File scripts\phase2-up.ps1

# The same by hand
helm upgrade --install vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml --wait --timeout 6m
helm test vigil -n vigil                         # runs pod vigil-test-api: curl /readyz

# What is installed?
helm list -n vigil
helm status vigil -n vigil
helm get values vigil -n vigil                   # values you supplied
helm get manifest vigil -n vigil                 # the YAML that was applied

# Change one setting -> new revision; checksum/config makes the pods restart
helm upgrade vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml --set config.hostMinGapMs=4000 --wait

# History and rollback
helm history vigil -n vigil
helm rollback vigil 1 -n vigil --wait

# Remove it (Phase 3 redeploys with Argo CD)
helm uninstall vigil -n vigil
kubectl delete namespace vigil
```

## 5. Phase 3: Argo CD

```powershell
# Install Argo CD (the only manual install), apply the app of apps, apply the node DNS fix
powershell -ExecutionPolicy Bypass -File scripts\phase3-up.ps1

# The same by hand
helm repo add argo https://argoproj.github.io/argo-helm
helm upgrade --install argocd argo/argo-cd --version 10.9.4 -n argocd --create-namespace -f gitops/bootstrap/argocd-values.yaml --wait
kubectl apply -f gitops/bootstrap/root-app.yaml
```

**Argo CD UI:** <http://argocd.localhost:8081>, user `admin`. Get the password (it is
printed only on your screen, never stored in git):

```powershell
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}")))
```

```bash
kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath="{.data.password}" | base64 -d; echo
```

App status and sync (no `argocd` CLI needed - an Application is a Kubernetes object):

```powershell
# All apps: root, keda, sealed-secrets, vigil-dev, vigil-prod -> Synced / Healthy
kubectl get applications -n argocd

# Details: which git revision, which objects, any errors
kubectl describe application vigil-dev -n argocd
kubectl get application vigil-dev -n argocd -o jsonpath="{.status.sync.revision}"

# Re-read git now instead of waiting up to 60 s
kubectl annotate application vigil-dev -n argocd argocd.argoproj.io/refresh=normal --overwrite

# Start a sync now
kubectl patch application vigil-dev -n argocd --type merge -p '{\"operation\":{\"initiatedBy\":{\"username\":\"admin\"},\"sync\":{\"revision\":\"main\"}}}'

# Prod has prune off: delete objects removed from git only when YOU say so
kubectl patch application vigil-prod -n argocd --type merge -p '{\"operation\":{\"initiatedBy\":{\"username\":\"admin\"},\"sync\":{\"revision\":\"main\",\"prune\":true}}}'
```

In Git Bash the patch JSON needs no backslashes:
`kubectl patch application vigil-dev -n argocd --type merge -p '{"operation":{"initiatedBy":{"username":"admin"},"sync":{"revision":"main"}}}'`

The environments:

```powershell
kubectl get pods -n vigil-dev
kubectl get pods -n vigil-prod
kubectl get ns vigil-dev --show-labels           # Pod Security labels set by managedNamespaceMetadata
kubectl get sealedsecret,secret -n vigil-prod    # the SealedSecret and the Secret the controller made
curl.exe -s -o NUL -w "dev %{http_code}`n" http://vigil-dev.localhost:8081/
curl.exe -s -o NUL -w "prod %{http_code}`n" http://vigil-prod.localhost:8081/
curl.exe -s -o NUL -w "argocd %{http_code}`n" http://argocd.localhost:8081/healthz
```

GitOps demos:

```powershell
# Self-heal: change the cluster by hand, Argo CD puts it back within seconds
kubectl scale deploy vigil-frontend -n vigil-dev --replicas=5
kubectl get deploy vigil-frontend -n vigil-dev -w        # 5 -> back to 2  (Ctrl+C to stop)

# A change through git: edit charts/vigil/values-dev.yaml, commit, push -> applied within ~1 minute
# Undo it the GitOps way:
git revert <commit>
```

Pushing from this PC (the Windows credential manager holds a token for a different
GitHub account, so use the gh login for that one command without changing any config):

```bash
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main --tags
```

## 6. Phase 4: CI/CD

```bash
# Recent runs of all workflows
gh run list --limit 10

# Follow a run live; exit code != 0 if it fails
gh run watch <run-id> --exit-status

# Only the CI workflow / the latest run's logs of a failed job
gh run list --workflow ci.yaml --limit 5
gh run view <run-id> --log-failed

# Which image tag does dev run? (written by the bump-dev job)
grep tag charts/vigil/values-dev-images.yaml
kubectl get deploy -n vigil-dev -o wide

# Promote the tag dev runs to prod: opens a pull request (merging it = deploying to prod)
gh workflow run promote.yaml
gh workflow run promote.yaml -f tag=sha-5f612bf          # or a specific tag
gh pr list
gh pr view <number> --web

# Are the GHCR images public? (the cluster pulls without credentials)
gh api "/users/gagan046-dev/packages?package_type=container" --jq '.[]|[.name,.visibility]|@tsv'
```

Local pre-check with the same tools CI uses (in a scratch venv outside the repo):

```bash
cd app
pip install -r requirements-dev.txt
ruff check . && ruff format --check . && pytest
```

A pull request opened by the promote workflow shows CI as `action_required` until a
human clicks **Approve and run workflows** on the PR page.

Promoting a release that contains new features (for example Compare) to prod:

```bash
gh workflow run promote.yaml                    # opens "promote sha-xxxxxxx to prod"
gh pr list                                      # find the number; its diff must only touch values-prod-images.yaml
gh pr view <number> --web                       # review it, approve CI, then merge
gh pr merge <number> --squash                   # merging IS the deployment to prod
# Argo CD syncs vigil-prod within about a minute:
kubectl get deploy -n vigil-prod -o wide        # all images show the new sha- tag
gh pr close <older-number>                      # close older promotion PRs that are now out of date
```

`vigil-prod` reads the chart from `main` but its image tags from the merged PR. So chart
changes (like the SearXNG pod) are live in prod as soon as they are on `main`, while the
new application code only arrives with the merge.

## 7. Using Vigil and the demo shop

Dev: `http://vigil-dev.localhost:8081` · Shop: `http://demo.localhost:8081` · Shop admin:
`http://demo.localhost:8081/admin` · Prod: `http://vigil-prod.localhost:8081`.

```powershell
# Full scripted demo against dev (plan -> dry run -> create watch -> drop price -> wait for notification)
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Vigil http://vigil-dev.localhost:8081 -Namespace vigil-dev
# ... and delete the watch afterwards
powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Vigil http://vigil-dev.localhost:8081 -Namespace vigil-dev -Cleanup

# System status (env, llm_mode, scheduler, min interval, LLM budget used)
curl.exe -s http://vigil-dev.localhost:8081/api/system

# Demo shop admin
curl.exe -s -X POST http://demo.localhost:8081/admin/reset                       # price Rs 1,34,990, layout 1
curl.exe -s http://demo.localhost:8081/admin/state
curl.exe -s -X POST -H "Content-Type: application/json" -d '{\"value\":114990}' http://demo.localhost:8081/admin/price

# Watches, logbook, inbox
curl.exe -s http://vigil-dev.localhost:8081/api/errands
curl.exe -s "http://vigil-dev.localhost:8081/api/log"
curl.exe -s "http://vigil-dev.localhost:8081/api/inbox?page=1&per_page=10"       # lists are paged: {items,total,page,per_page,pages}
curl.exe -s "http://vigil-dev.localhost:8081/api/log?page=2&per_page=50&status=error"
curl.exe -s http://vigil-dev.localhost:8081/api/inbox/unread-count
curl.exe -s -X POST http://vigil-dev.localhost:8081/api/inbox/read-all

# The CronJobs the api created (one per watch) and the Jobs they started
kubectl get cronjobs -n vigil-dev -l app.kubernetes.io/managed-by=vigil-api
kubectl get jobs -n vigil-dev
```

Sending text with the rupee sign: write the JSON into a UTF-8 file and send it with
`--data-binary @file` (the Windows console can mangle `₹` on the command line):

```bash
printf '%s' '{"text":"Tell me when http://vigil-demo-shop:8000/static drops below ₹1,20,000, check every minute"}' > plan.json
curl -sS -X POST -H 'Content-Type: application/json' --data-binary @plan.json http://vigil-dev.localhost:8081/api/plan
rm plan.json
```

### Price compare and SearXNG

Dev uses the offline demo provider (fictional sellers; only "Aurora 15" finds anything).
Prod uses SearXNG and the real shops. Write JSON with `₹` or other non-ASCII text into a
UTF-8 file, as above.

```bash
# Start a comparison (202 + a run id), then read it; status goes queued -> searching -> fetching -> done
printf '%s' '{"query":"Aurora 15"}' > cmp.json
curl -sS -X POST -H 'Content-Type: application/json' --data-binary @cmp.json http://vigil-dev.localhost:8081/api/compare
curl -s http://vigil-dev.localhost:8081/api/compare/<run-id>
curl -s "http://vigil-dev.localhost:8081/api/compare?page=1&per_page=10"      # past comparisons
rm cmp.json

# Keep watching the best price: target in rupees, check every 60 minutes (the default)
printf '%s' '{"threshold":120000,"every_minutes":60}' > track.json
curl -sS -X POST -H 'Content-Type: application/json' --data-binary @track.json http://vigil-dev.localhost:8081/api/compare/<run-id>/track
rm track.json

# Make one demo seller cheaper, then compare again (sellers: pixelmart bazaar99 northwind bytebay quietcart)
curl -sS -X POST -H 'Content-Type: application/json' -d '{"price":115000}' http://demo.localhost:8081/admin/sellers/bazaar99
curl -sS -X POST http://demo.localhost:8081/admin/reset

# Which search provider and sellers are configured?
curl -s http://vigil-dev.localhost:8081/api/system
```

```bash
# Prod only: the SearXNG pod, its Service and who may talk to it
kubectl get deploy,svc,pod -n vigil-prod -l app.kubernetes.io/component=searxng
kubectl logs -n vigil-prod deploy/vigil-searxng --tail=30
kubectl get networkpolicy -n vigil-prod                                     # look for allow-searxng
kubectl get configmap vigil-config -n vigil-prod -o jsonpath='{.data.SEARCH_PROVIDER}'
kubectl get secret vigil-secrets -n vigil-prod -o jsonpath='{.data}'        # key NAMES only matter: check searxng-secret exists, never decode it

# Try real search in dev (RAM +~200 MB): in charts/vigil/values-dev.yaml set
#   config.searchProvider: searxng, searxng.enabled: true, secrets.searxngSecret: <random>
# commit + push (Argo CD applies it). Never commit a real secret: use a throw-away random value.
```

Add or rotate the `searxng-secret` key in the prod SealedSecret (the value never touches disk):

```bash
V=$(openssl rand -hex 32)
kubectl create secret generic vigil-secrets -n vigil-prod --from-literal=searxng-secret="$V"   --dry-run=client -o yaml | kubeseal --controller-namespace kube-system   --controller-name sealed-secrets-controller --format yaml   --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
unset V
kubectl apply --dry-run=server -f gitops/envs/prod/vigil-secrets.sealed.yaml
```

## 8. Debugging

```powershell
# Overview
kubectl get all -n vigil-dev
kubectl get pods -n vigil-dev -o wide                  # which node each pod is on

# Why is a pod Pending / CrashLoopBackOff / ImagePullBackOff? Read "Events" at the bottom
kubectl describe pod <pod> -n vigil-dev

# Recent events in the namespace, newest last
kubectl get events -n vigil-dev --sort-by=.lastTimestamp

# Logs
kubectl logs deploy/vigil-api -n vigil-dev
kubectl logs deploy/vigil-api -n vigil-dev -c migrate          # the initContainer
kubectl logs deploy/vigil-worker -n vigil-dev -f               # follow (Ctrl+C to stop)
kubectl logs <pod> -n vigil-dev --previous                     # the crashed container before the restart
kubectl logs -n vigil-dev -l app.kubernetes.io/component=notifier

# Look inside
kubectl exec -it vigil-postgres-0 -n vigil-dev -- psql -U vigil -d vigil
kubectl exec vigil-redis-0 -n vigil-dev -- redis-cli LLEN vigil:checks
kubectl exec deploy/vigil-api -n vigil-dev -- env              # careful: shows secret env values too

# Reach a Service from your PC without the ingress (use a free local port)
kubectl port-forward svc/vigil-api -n vigil-dev 18000:8000
#   then: curl.exe -s http://127.0.0.1:18000/readyz   and   http://127.0.0.1:18000/metrics

# CPU / RAM (k3s ships metrics-server)
kubectl top nodes
kubectl top pods -n vigil-dev

# Rendered config and rollout state
kubectl get configmap vigil-config -n vigil-dev -o yaml
kubectl rollout status deploy/vigil-api -n vigil-dev
kubectl rollout history deploy/vigil-api -n vigil-dev
kubectl rollout restart deploy/vigil-notifier -n vigil-dev     # restart pods (re-read env vars)

# Who may do what (RBAC)
kubectl auth can-i create cronjobs -n vigil-dev --as system:serviceaccount:vigil-dev:vigil-api   # yes
kubectl auth can-i create pods -n vigil-dev --as system:serviceaccount:vigil-dev:vigil-api       # no

# Network policies in force
kubectl get networkpolicy -n vigil-dev

# Argo CD's own logs when an app will not sync
kubectl logs -n argocd statefulset/argocd-application-controller --tail 50
kubectl logs -n argocd deploy/argocd-repo-server --tail 50
```

## 9. Watching KEDA scale

```powershell
# The ScaledObject and the HPA KEDA created for it (keda-hpa-vigil-worker)
kubectl get scaledobject,hpa -n vigil-dev

# Watch worker pods appear and disappear (dev: min 0, max 5)
kubectl get pods -n vigil-dev -l app.kubernetes.io/component=worker -w

# How many checks are waiting in the queue KEDA looks at?
kubectl exec vigil-redis-0 -n vigil-dev -- redis-cli LLEN vigil:checks

# Why did it (not) scale?
kubectl describe scaledobject vigil-worker -n vigil-dev
kubectl logs -n keda deploy/keda-operator --tail 30

# Trigger work: queue a check for a watch right now
curl.exe -s -X POST http://vigil-dev.localhost:8081/api/errands/<errand-id>/check
```

## 10. Cleanup and freeing RAM

```powershell
# Biggest win: stop the whole cluster when you are not using it (everything is kept)
k3d cluster stop vigil

# See what uses memory
kubectl top pods -A --sort-by=memory
docker stats --no-stream

# Delete a watch (and its CronJob)
curl.exe -s -X DELETE http://vigil-dev.localhost:8081/api/errands/<errand-id>

# Remove finished trigger Jobs
kubectl delete jobs -n vigil-dev --field-selector status.successful=1

# Remove one environment for good: delete its file from gitops/apps/ and push
# (manual "kubectl scale" or "kubectl delete" is undone by Argo CD's selfHeal)
git rm gitops/apps/vigil-prod.yaml
git commit -m "chore: remove prod environment for now"

# Old local images
docker image prune

# Everything (cluster, volumes, Argo CD) - cannot be undone
k3d cluster delete vigil
```

## 11. Your actions: secrets and notifications

These are the steps left for you. **Never** put a real key in a values file, a commit
message, an issue or the chat; the repo is public.

### Install kubeseal (once)

Download `kubeseal-0.40.0-windows-amd64.tar.gz` from
<https://github.com/bitnami-labs/sealed-secrets/releases/tag/v0.40.0> into a folder
**outside** the repo (for example `C:\Tools\kubeseal`), extract it and check:

```powershell
tar -xzf kubeseal-0.40.0-windows-amd64.tar.gz kubeseal.exe
.\kubeseal.exe --version            # kubeseal version: 0.40.0
```

Add that folder to your PATH (or call it with its full path below). kubeseal fetches
the public key from the controller `sealed-secrets-controller` in `kube-system`
automatically.

### Add the OpenRouter key to prod as a SealedSecret

1. Create a free key at <https://openrouter.ai/keys>.
2. Seal it straight into the existing file. The key is typed into a hidden prompt, goes
   through the pipe to kubeseal, and is never shown, saved or put in shell history:

```powershell
$sec = Read-Host "OpenRouter key" -AsSecureString
$key = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
kubectl create secret generic vigil-secrets -n vigil-prod --from-literal=openrouter-api-key=$key --dry-run=client -o yaml |
    kubeseal --format yaml --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
Remove-Variable key, sec
```

```bash
# Git Bash version: read -s hides what you type
read -rs -p "OpenRouter key: " KEY; echo
kubectl create secret generic vigil-secrets -n vigil-prod --from-literal=openrouter-api-key="$KEY" \
  --dry-run=client -o yaml | kubeseal --format yaml --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
unset KEY
```

3. Check before committing: the diff must show only a new **encrypted** line
   `openrouter-api-key: AgB...`, and the plain key must not be in the file:

```powershell
git diff gitops/envs/prod/vigil-secrets.sealed.yaml
Select-String -Path gitops/envs/prod/vigil-secrets.sealed.yaml -Pattern "sk-or-"      # must print nothing
```

4. Commit, push, and restart the gateway (env vars are read at start):

```powershell
git add gitops/envs/prod/vigil-secrets.sealed.yaml
git commit -m "chore(prod): add OpenRouter key (sealed)"
git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push origin main
# wait ~1 minute for Argo CD, then:
kubectl get secret vigil-secrets -n vigil-prod -o jsonpath="{.data}" | Select-String openrouter-api-key   # key name present
kubectl rollout restart deploy/vigil-llm-gateway -n vigil-prod
```

5. Try it: a vague request makes the planner ask the model
   (`source` should now be `"llm"` instead of `"rules"`):

```powershell
curl.exe -s -X POST -H "Content-Type: application/json" -d '{\"text\":\"keep an eye on https://example.com/board for me\"}' http://vigil-prod.localhost:8081/api/plan
kubectl logs deploy/vigil-llm-gateway -n vigil-prod --tail 20
```

Rotating the key later: run step 2 again with the new key (merge replaces the value).

### Re-seal everything for a NEW cluster

A recreated cluster has a new sealed-secrets key pair, so the old file cannot be
decrypted. Make a fresh one (Git Bash; random values exist only in the pipe):

```bash
kubectl create secret generic vigil-secrets -n vigil-prod \
  --from-literal=postgres-password="$(openssl rand -hex 24)" \
  --from-literal=internal-token="$(openssl rand -hex 32)" \
  --from-literal=llm-master-key="sk-$(openssl rand -hex 24)" \
  --dry-run=client -o yaml | kubeseal --format yaml > gitops/envs/prod/vigil-secrets.sealed.yaml
```

Then keep the comment header (restore it with `git diff` / your editor), add the
OpenRouter key as above, commit and push. If `vigil-prod`'s Postgres volume already
exists with the old password, delete it so it is recreated with the new one:
`kubectl delete pvc -n vigil-prod -l app.kubernetes.io/component=postgres --wait=false; kubectl delete pod vigil-postgres-0 -n vigil-prod`
(prod data is lost; the PVC is only removed once the pod using it is gone, and the
StatefulSet then recreates both).

### Turn on push notifications with ntfy

[ntfy](https://ntfy.sh) is a free push service: anyone who knows a **topic** name can
publish to it and subscribe to it. Vigil's notifier posts every notification to
`NTFY_URL` when it is set.

1. Make an unguessable topic name and subscribe to it in the ntfy app (Android/iOS) or
   at `https://ntfy.sh/<topic>` in a browser:

```powershell
"vigil-" + [guid]::NewGuid().ToString("N")          # e.g. vigil-3f9c...; copy it
curl.exe -d "hello from my PC" https://ntfy.sh/<topic>   # test: your phone should buzz
```

2. Set it in the values of the environment you want (`config.ntfyUrl` becomes the
   ConfigMap key `NTFY_URL`; the `checksum/config` annotation restarts the notifier):

```yaml
# charts/vigil/values-dev.yaml (or values-prod.yaml)
config:
  ntfyUrl: https://ntfy.sh/<topic>
```

3. Commit and push; Argo CD applies it within about a minute. Check:

```powershell
kubectl get configmap vigil-config -n vigil-dev -o jsonpath="{.data.NTFY_URL}"
kubectl logs deploy/vigil-notifier -n vigil-dev | Select-String "notifier started"   # ntfy=True
```

Be aware: the topic lives in a ConfigMap and therefore in the **public** repo, so anyone
who reads the repo could subscribe and see your alerts. Fine for demo-shop alerts; for
anything private use Telegram below, whose token goes into the sealed Secret. For a quick
local test without committing (Phase 2 Helm install only - Argo CD would undo it):
`helm upgrade vigil charts/vigil -n vigil -f charts/vigil/values-dev.yaml --reuse-values --set config.ntfyUrl=https://ntfy.sh/<topic>`.

### Optional: Telegram (private) for prod

Create a bot with @BotFather, get your chat id, then seal both keys the same way as the
OpenRouter key:

```bash
read -rs -p "Bot token: " TOK; echo; read -r -p "Chat id: " CHAT
kubectl create secret generic vigil-secrets -n vigil-prod \
  --from-literal=telegram-bot-token="$TOK" --from-literal=telegram-chat-id="$CHAT" \
  --dry-run=client -o yaml | kubeseal --format yaml --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
unset TOK CHAT
# commit + push, then: kubectl rollout restart deploy/vigil-notifier -n vigil-prod
```

## 12. Git: tags and history

```bash
git tag                                          # phase-1 phase-2 phase-3 phase-4
git show phase-1 --stat                          # what Phase 1 added
git diff phase-1 phase-2 -- k8s charts           # raw YAML -> Helm chart
git diff phase-2 phase-3 -- gitops charts        # Helm -> Argo CD
git diff phase-3 phase-4 -- .github charts       # CI/CD
git checkout phase-2                             # look at the repo as it was (git switch main to return)
git log --oneline --decorate
```
