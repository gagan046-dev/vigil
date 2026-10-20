<#
.SYNOPSIS
  Creates the Kubernetes Secret "vigil-secrets" with RANDOM values.

.DESCRIPTION
  The values are generated here and sent straight to the cluster; they are never
  printed and never written to a file, so nothing secret can end up in git.

  If the Secret already exists it is left alone (changing the postgres password
  after the database was created would lock the apps out). Use -Force to replace
  it - only do that together with deleting the postgres volume.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\create-dev-secret.ps1
  powershell -ExecutionPolicy Bypass -File scripts\create-dev-secret.ps1 -Namespace vigil-dev
#>
param(
    [string]$Namespace = "vigil",
    [string]$Name = "vigil-secrets",
    [switch]$Force
)
$ErrorActionPreference = "Stop"

function New-RandomToken([int]$Bytes = 32) {
    # Cryptographically secure random bytes -> URL-safe text (no + / =)
    $buf = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    return ([Convert]::ToBase64String($buf)).Replace("+", "-").Replace("/", "_").TrimEnd("=")
}

# Does it exist already? (--ignore-not-found prints nothing when it does not)
$exists = [bool](kubectl get secret $Name -n $Namespace --ignore-not-found -o name)
if ($exists -and -not $Force) {
    Write-Host "Secret $Namespace/$Name already exists - keeping it (use -Force to replace)."
    exit 0
}

$pg = New-RandomToken 24
$tok = New-RandomToken 32

# --dry-run=client -o yaml builds the Secret locally; "kubectl apply -f -" sends it.
# The values only travel through the pipe, never to the screen or disk.
kubectl create secret generic $Name -n $Namespace `
    --from-literal=postgres-password=$pg `
    --from-literal=internal-token=$tok `
    --dry-run=client -o yaml | kubectl apply -f - | Out-Null
kubectl label secret $Name -n $Namespace app.kubernetes.io/part-of=vigil --overwrite | Out-Null

Remove-Variable pg, tok
Write-Host "Secret $Namespace/$Name created with random values (keys: postgres-password, internal-token)."
