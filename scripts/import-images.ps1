<#
.SYNOPSIS
  Copies local Docker images into every node of the k3d cluster "vigil".

.DESCRIPTION
  The cluster cannot pull "vigil-core:dev" etc. from a registry (they only exist
  on this PC), so they must be copied in.

  Why not "k3d image import"? With Docker Desktop's containerd image store it can
  report success while importing nothing. This script streams "docker save" into
  containerd ("ctr images import") on each node instead, which works reliably.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1
  powershell -ExecutionPolicy Bypass -File scripts\import-images.ps1 -Images vigil-core:dev
#>
param(
    [string]$Cluster = "vigil",
    [string[]]$Images = @(
        "vigil-core:dev", "vigil-worker:dev", "vigil-frontend:dev",
        "postgres:17.11-alpine", "redis:8.2.10-alpine", "curlimages/curl:8.22.0"
    )
)
$ErrorActionPreference = "Stop"

$nodes = docker ps --filter "label=k3d.cluster=$Cluster" --filter "label=k3d.role=server" --format "{{.Names}}"
$nodes = @($nodes) + @(docker ps --filter "label=k3d.cluster=$Cluster" --filter "label=k3d.role=agent" --format "{{.Names}}")
if (-not $nodes) { throw "No running nodes found for k3d cluster '$Cluster'" }

foreach ($img in $Images) {
    docker image inspect $img *> $null
    if ($LASTEXITCODE -ne 0) { Write-Host "pulling $img"; docker pull -q $img | Out-Null }
    $tar = Join-Path $env:TEMP ("vigil-img-" + [guid]::NewGuid().ToString("N") + ".tar")
    docker save -o $tar $img
    try {
        foreach ($n in $nodes) {
            docker cp $tar "${n}:/tmp/img.tar" | Out-Null
            docker exec $n ctr -n k8s.io images import /tmp/img.tar | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "import of $img into $n failed" }
            docker exec $n rm -f /tmp/img.tar | Out-Null
            Write-Host "imported $img -> $n"
        }
    } finally {
        Remove-Item $tar -Force -ErrorAction SilentlyContinue
    }
}
