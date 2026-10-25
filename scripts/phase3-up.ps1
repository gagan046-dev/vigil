<#
.SYNOPSIS
  Phase 3: installs Argo CD and hands everything else over to GitOps.

.DESCRIPTION
  1. helm install Argo CD (chart argo-cd 10.9.4) into namespace argocd.
  2. kubectl apply the root "app of apps"; Argo CD then deploys KEDA, sealed-secrets,
     vigil-dev and vigil-prod from https://github.com/gagan046-dev/vigil (branch main).
  3. Prints how to read the admin password.

  Needs: the k3d cluster "vigil" running (cluster/k3d-config.yaml) and the local images
  imported (scripts/import-images.ps1, plus ghcr.io/berriai/litellm:v1.103.2 for prod).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\phase3-up.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

# Docker Desktop's DNS (192.168.65.254) times out on AAAA queries for ghcr.io, so
# containerd in the k3d nodes fails with "lookup ghcr.io: Try again". This installs a
# k3d start-up script in every node that adds a second nameserver at each boot
# (Docker rewrites /etc/resolv.conf on every container start, so a one-off edit is lost).
& (Join-Path $PSScriptRoot "install-node-dns-fix.ps1")

helm repo add argo https://argoproj.github.io/argo-helm --force-update | Out-Null
helm upgrade --install argocd argo/argo-cd --version 10.9.4 -n argocd --create-namespace `
    -f "$root\gitops\bootstrap\argocd-values.yaml" --wait --timeout 10m
if ($LASTEXITCODE -ne 0) { throw "Argo CD install failed" }

kubectl apply -f "$root\gitops\bootstrap\root-app.yaml"
if ($LASTEXITCODE -ne 0) { throw "applying the root app failed" }

Write-Host ""
Write-Host "Argo CD UI : http://argocd.localhost:8081  (user: admin)"
Write-Host "Password   : [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String((kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}')))"
Write-Host "Status     : kubectl get applications -n argocd"
Write-Host "Dev UI     : http://vigil-dev.localhost:8081   Demo shop: http://demo.localhost:8081"
Write-Host "Prod UI    : http://vigil-prod.localhost:8081"
