<#
.SYNOPSIS
  Installs the node DNS fix permanently into every node of the k3d cluster "vigil".
.DESCRIPTION
  Copies cluster/k3d-entrypoint-zz-dns-fallback.sh to /bin in each node container.
  k3d runs every /bin/k3d-entrypoint-*.sh at each node start, so the fallback
  nameserver is re-added after every "k3d cluster stop/start" or Docker restart.
  The file lives in the node container, so run this again only after the cluster
  is recreated ("k3d cluster delete" + "create"). It also applies the fix right now.
.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\install-node-dns-fix.ps1
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "cluster\k3d-entrypoint-zz-dns-fallback.sh"
$target = "/bin/k3d-entrypoint-zz-dns-fallback.sh"

$nodes = docker ps --filter "label=k3d.cluster=vigil" --format "{{.Names}}" |
    Where-Object { $_ -notmatch "serverlb|tools" }
if (-not $nodes) { throw "no running nodes for cluster 'vigil' (k3d cluster start vigil)" }

foreach ($n in $nodes) {
    docker cp $script "${n}:$target"
    if ($LASTEXITCODE -ne 0) { throw "copy to $n failed" }
    docker exec $n sh -c "chmod 0755 $target && $target"   # install, then apply now
    if ($LASTEXITCODE -ne 0) { throw "running the fix on $n failed" }
    Write-Host "$n : DNS fallback installed"
}
