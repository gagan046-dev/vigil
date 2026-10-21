# Price compare, paged APIs and the new UI: deploy + live verification

Date: 2026-10-21. Cluster: k3d `vigil` (Argo CD, KEDA, sealed-secrets).
Commits: `a930f37` feat(compare), `cca686e` feat(ui), `cbcfd30` chore(chart),
`565c4f5` fix(ui). CI bumped dev to `sha-565c4f5` (`3bbd2dc`).
Promotion PR to prod (not merged): https://github.com/gagan046-dev/vigil/pull/2

## How a comparison flows

```
 Browser  --POST /api/compare-->  vigil-api  --RPUSH {type:"compare"}-->  Redis list vigil:checks
                                                                              |
                                        KEDA scales the worker on that list   v
 Browser <--GET /api/compare/{id}--  Postgres compare_runs  <--save--  vigil-worker
   (polls while searching/fetching)                                      |   |
                                                       search provider --+   +-- fetch seller pages
                                       dev:  demo  (fictional demo-shop sellers, offline)
                                       prod: searxng  (vigil-searxng pod, JSON API)
```

## 1. Helm chart

New in `charts/vigil`:

| What | Where |
|---|---|
| `vigil-searxng` Deployment + Service + ConfigMap (`settings.yml`: JSON format on, limiter and image proxy off) | `templates/searxng.yaml` |
| `SEARCH_PROVIDER`, `SEARXNG_URL`, `COMPARE_SELLERS`, `COMPARE_MAX_PAGES`, `DEMO_SHOP_URL` (dev) | `templates/configmap.yaml`, `config.*` in `values.yaml` |
| NetworkPolicy `allow-searxng` (only the worker may connect, port 8080) + a worker egress rule to it | `templates/networkpolicies.yaml` |
| Secret key `searxng-secret` (SearXNG reads it as `SEARXNG_SECRET`) | dev: `secrets.searxngSecret`; prod: SealedSecret |

- **Dev:** `searchProvider: demo`, `searxng.enabled: false`. The demo provider needs no web search, and this saves about 200 MB of RAM.
- **Prod:** `searchProvider: searxng`, `searxng.enabled: true`.

SearXNG runs as the image's own user 977 with a read-only root filesystem and all capabilities dropped. This was tested first with `docker run --read-only --user 977:977 --cap-drop ALL`: `/healthz` returned 200 and `/search?format=json` returned results.

```
helm lint charts/vigil -f charts/vigil/values-dev.yaml     # 1 chart(s) linted, 0 chart(s) failed
helm lint charts/vigil -f charts/vigil/values-prod.yaml    # 1 chart(s) linted, 0 chart(s) failed
helm template v charts/vigil -n vigil-prod -f values-prod.yaml -f values-prod-images.yaml   # renders vigil-searxng
```

Sealing the prod key. The random value lives only in a shell variable and goes straight into kubeseal; the plaintext is never written to a file:

```
V=$(openssl rand -hex 32)
kubectl create secret generic vigil-secrets -n vigil-prod --from-literal=searxng-secret="$V" \
  --dry-run=client -o yaml | kubeseal --controller-namespace kube-system \
  --controller-name sealed-secrets-controller --format yaml \
  --merge-into gitops/envs/prod/vigil-secrets.sealed.yaml
unset V
kubectl apply --dry-run=server -f gitops/envs/prod/vigil-secrets.sealed.yaml   # configured (server dry run)
```

Secret scan: gitleaks 8.30.1 on the staged diff reported one `generic-api-key` hit. It is the new **encrypted** `searxng-secret` value in the SealedSecret, so it is a false positive. Nothing else was found.

## 2. CI and Argo CD

```
gh run watch <id>      # ci on cbcfd30: lint+test, helm, 3 image builds, bump dev - all success
                       # ci on 565c4f5: all success
kubectl get deploy -n vigil-dev -o wide
  vigil-api        2/2  ghcr.io/gagan046-dev/vigil-core:sha-565c4f5
  vigil-demo-shop  1/1  ghcr.io/gagan046-dev/vigil-core:sha-565c4f5
  vigil-frontend   2/2  ghcr.io/gagan046-dev/vigil-frontend:sha-565c4f5
  vigil-notifier   1/1  ghcr.io/gagan046-dev/vigil-core:sha-565c4f5
  vigil-worker     1/1  ghcr.io/gagan046-dev/vigil-worker:sha-565c4f5
```

Note: `vigil-prod` takes the chart from `main` and only its image tags from the PR. So the chart part is already live in prod: `vigil-searxng` 1/1 Running, the Secret has `searxng-secret`, and `SEARCH_PROVIDER=searxng`. The old prod images ignore the new settings until PR #2 is merged.

## 3. Live check on http://vigil-dev.localhost:8081

The test is a Playwright script driving the installed Chrome, kept in the session scratchpad (`live_e2e.py`).

| Check | Result |
|---|---|
| Watches, Compare, Inbox, Activity, System | All load; page titles are right; 0 console errors |
| Mobile, 375 px | `scrollWidth` 375 (no sideways scrolling) |
| New watch through the 4 steps | Dry run found ₹1,34,990 (`span.price-now`); saved; watch count went from 1 to 2 |
| Compare "Aurora 15" (about 20 s) | Best: **Bazaar99 ₹1,24,499** (meta tags)<br>PixelMart ₹1,29,990 (JSON-LD, 13% off)<br>Northwind ₹1,31,000 (`rendered=True`, price found after the JavaScript render)<br>ByteBay rejected as "different product … variant: pro"<br>QuietCart "blocked by robots.txt" |
| Track best price | Threshold ₹1,20,000 → errand created and CronJob `errand-a6e5d053739d` (`0 * * * *`) |
| Alert | Bazaar99 price lowered to 115000 through the demo-shop admin API → unread count 2 → 3, badge 3<br>Inbox entry: "Best price: Aurora 15 RTX 5070 15.6" Gaming Laptop at Bazaar99 ₹1,15,000 (target < ₹1,20,000)"<br>Mark all as read → count 0, badge 0 |
| Activity paging (110 checks) | "1–50 of 110 checks", then Next shows "51–100 of 110 checks", URL `#/activity?page=2` |
| Cleanup | Bazaar99 price restored; the 1-minute tracking watch paused |

The live check found two layout problems, fixed in `565c4f5` and checked again after Argo synced:

- The Activity filter dropdowns stretched to the full page width.
- The chart printed the same HH:MM five times when the history covered only a few minutes.

## Known issues

- In the first ("baseline") check of the tracking watch, Bazaar99 failed once with `could not fetch the page (RemoteProtocolError)`. This is a one-off connection drop from the single demo-shop pod. The fetcher does not retry it, and the next check worked.
- SearXNG search was not run end to end inside the cluster: dev uses the demo provider, and prod still runs the old images until PR #2 is merged. The backend agent tested real SearXNG once, in Docker.
