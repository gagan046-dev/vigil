<#
.SYNOPSIS
  Phase 2: installs (or upgrades) Vigil with the Helm chart in charts/vigil.

.DESCRIPTION
  1. creates the namespace and labels it for the Pod Security Standard "restricted"
     ("helm install --create-namespace" would create it WITHOUT these labels)
  2. helm upgrade --install with the chosen values file, waiting until every pod is ready
  3. helm test (the api's /readyz through its Service)

  Prerequisites: the k3d cluster with the vigil images imported
  (scripts/import-images.ps1) and KEDA installed (namespace keda).
  Do not install into a namespace that still holds the Phase-1 raw YAML objects
  (their selectors differ, so Helm cannot take them over): kubectl delete namespace vigil

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\phase2-up.ps1
  powershell -ExecutionPolicy Bypass -File scripts\phase2-up.ps1 -Values prod -Namespace vigil-prod
#>
param(
    [string]$Namespace = "vigil",
    [string]$Release = "vigil",
    [ValidateSet("dev", "prod")][string]$Values = "dev"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$chart = Join-Path $root "charts\vigil"

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Yellow }

Step "Namespace $Namespace (Pod Security 'restricted' enforced)"
kubectl create namespace $Namespace --dry-run=client -o yaml | kubectl apply -f - | Out-Null
kubectl label namespace $Namespace --overwrite `
    app.kubernetes.io/part-of=vigil `
    pod-security.kubernetes.io/enforce=restricted pod-security.kubernetes.io/enforce-version=latest `
    pod-security.kubernetes.io/warn=restricted pod-security.kubernetes.io/warn-version=latest `
    pod-security.kubernetes.io/audit=restricted pod-security.kubernetes.io/audit-version=latest | Out-Null

Step "helm upgrade --install $Release (values-$Values.yaml)"
helm upgrade --install $Release $chart -n $Namespace `
    -f (Join-Path $chart "values-$Values.yaml") --wait --timeout 6m
if ($LASTEXITCODE -ne 0) { throw "helm upgrade --install failed" }

Step "helm test"
helm test $Release -n $Namespace
if ($LASTEXITCODE -ne 0) { throw "helm test failed" }

kubectl get pods,svc,ingress,scaledobject -n $Namespace
