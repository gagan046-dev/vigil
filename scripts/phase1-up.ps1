<#
.SYNOPSIS
  Phase 1: deploys Vigil from the raw YAML in k8s/ into namespace "vigil".

.DESCRIPTION
  Applies the manifests in dependency order and waits for each step:
    namespace -> config + secret -> postgres + redis -> api -> the rest.
  Prerequisites: the k3d cluster (cluster/k3d-config.yaml) with the vigil images
  imported (scripts/import-images.ps1) and KEDA installed (namespace keda).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\phase1-up.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$k8s = Join-Path $root "k8s"
$ns = "vigil"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Yellow }
function Apply($file) {
    kubectl apply -f (Join-Path $k8s $file)
    if ($LASTEXITCODE -ne 0) { throw "kubectl apply $file failed" }
}
function WaitRollout($kind, $name, $timeout = "240s") {
    kubectl rollout status "$kind/$name" -n $ns --timeout=$timeout
    if ($LASTEXITCODE -ne 0) { throw "$kind/$name did not become ready" }
}

Step "Namespace (Pod Security 'restricted' enforced)"
Apply "00-namespace.yaml"

Step "Configuration and secret"
Apply "01-configmap.yaml"
& (Join-Path $PSScriptRoot "create-dev-secret.ps1") -Namespace $ns

Step "Data stores: postgres + redis"
Apply "10-postgres.yaml"
Apply "11-redis.yaml"
WaitRollout statefulset vigil-postgres
WaitRollout statefulset vigil-redis

Step "API (runs the database migration in its initContainer)"
Apply "20-api.yaml"
WaitRollout deployment vigil-api

Step "Worker (+ KEDA autoscaling), notifier, demo shop, frontend"
Apply "21-worker.yaml"
Apply "22-notifier.yaml"
Apply "24-demo-shop.yaml"
Apply "23-frontend.yaml"   # after the api Service exists: nginx resolves it at start-up
WaitRollout deployment vigil-notifier
WaitRollout deployment vigil-demo-shop
WaitRollout deployment vigil-frontend

Step "Ingress and network policies"
Apply "30-ingress.yaml"
Apply "40-networkpolicies.yaml"

Step "Done"
kubectl get pods,svc,ingress,scaledobject -n $ns
Write-Host "`nUI:        http://vigil.localhost:8081"
Write-Host "Demo shop: http://demo.localhost:8081  (admin: http://demo.localhost:8081/admin)"
