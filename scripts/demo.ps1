<#
.SYNOPSIS
  End-to-end Vigil demo against the running cluster (Phase 1 or the Helm install).

.DESCRIPTION
  1. resets the demo shop            (http://demo.localhost:8081)
  2. asks Vigil to plan a watch from plain English
  3. dry-runs it (a worker fetches the page right now)
  4. creates the watch -> the api creates a Kubernetes CronJob for it
  5. drops the shop's price below the target
  6. waits until the notification shows up in the inbox

  Uses curl.exe (built into Windows 10/11). Note: "curl" without ".exe" in
  Windows PowerShell 5.1 is an alias for Invoke-WebRequest - not what we want.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1
  powershell -ExecutionPolicy Bypass -File scripts\demo.ps1 -Namespace vigil -Cleanup
#>
param(
    [string]$Vigil = "http://vigil.localhost:8081",
    [string]$Shop = "http://demo.localhost:8081",
    [string]$Namespace = "vigil",
    [int]$TimeoutSeconds = 240,
    [switch]$Cleanup
)
$ErrorActionPreference = "Stop"
# Show the rupee sign correctly and decode curl's UTF-8 output.
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Step($msg) { Write-Host "`n== $msg" -ForegroundColor Yellow }

# Calls the API with curl.exe. The JSON body goes through a temporary UTF-8 file
# so non-ASCII text (like the rupee sign) is sent correctly.
function Invoke-Api([string]$Method, [string]$Url, $Body = $null) {
    $cargs = @("-sS", "-m", "90", "-X", $Method, "-H", "Content-Type: application/json")
    $tmp = $null
    if ($null -ne $Body) {
        $tmp = [System.IO.Path]::GetTempFileName()
        [System.IO.File]::WriteAllText($tmp, ($Body | ConvertTo-Json -Depth 10 -Compress), $utf8NoBom)
        $cargs += @("--data-binary", "@$tmp")
    }
    try {
        $out = & curl.exe @cargs $Url
        if ($LASTEXITCODE -ne 0) { throw "curl failed ($LASTEXITCODE) for $Method $Url" }
    } finally {
        if ($tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
    if ($out) {
        $parsed = ($out -join "`n") | ConvertFrom-Json
        # PowerShell 5.1 hands a JSON array down the pipeline as ONE object;
        # emit the items one by one so Where-Object / Format-Table see rows.
        foreach ($item in @($parsed)) { $item }
    }
}

Step "System status"
Invoke-Api GET "$Vigil/api/system" | Format-List

Step "Reset the demo shop (price Rs 1,34,990, layout 1)"
Invoke-Api POST "$Shop/admin/reset" @{} | Out-Null
Write-Host "Shop admin panel: $Shop/admin"

$text = "Tell me when http://vigil-demo-shop:8000/static drops below Rs 1,20,000, check every minute"
Step "Plan: '$text'"
$plan = Invoke-Api POST "$Vigil/api/plan" @{ text = $text }
$plan.spec | Format-List
Write-Host ("source: {0}; notes: {1}" -f $plan.source, ($plan.notes -join " | "))

Step "Dry run (a worker checks the page now)"
$dry = Invoke-Api POST "$Vigil/api/dryrun" @{ spec = $plan.spec }
$dry | Select-Object ok, value, condition_true, rendered, selector, message | Format-List

Step "Keep watch (create the errand)"
$errand = Invoke-Api POST "$Vigil/api/errands" @{ spec = $plan.spec; prompt = $text }
Write-Host ("errand {0}  schedule '{1}'  condition '{2}'" -f $errand.id, $errand.schedule, $errand.condition)
kubectl get cronjobs -n $Namespace -l "vigil.dev/errand-id=$($errand.id)"

Step "Waiting for the first (baseline) check..."
$deadline = (Get-Date).AddSeconds(120)
do {
    Start-Sleep -Seconds 5
    $checks = @(Invoke-Api GET "$Vigil/api/errands/$($errand.id)/checks?limit=5")
} while ($checks.Count -eq 0 -and (Get-Date) -lt $deadline)
$checks | Select-Object status, value, message | Format-Table -AutoSize

Step "Drop the price to Rs 1,14,990 in the shop"
Invoke-Api POST "$Shop/admin/price" @{ value = 114990 } | Select-Object price | Format-List

Step "Waiting for the notification (CronJob fires every minute)..."
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$found = $null
while ((Get-Date) -lt $deadline -and -not $found) {
    Start-Sleep -Seconds 10
    $inbox = Invoke-Api GET "$Vigil/api/inbox?limit=50"
    $found = @($inbox | Where-Object { $_.errand_id -eq $errand.id }) | Select-Object -First 1
    Write-Host -NoNewline "."
}
Write-Host ""
if ($found) {
    Write-Host "NOTIFICATION: $($found.title)" -ForegroundColor Green
} else {
    Write-Host "No notification within $TimeoutSeconds s - check: kubectl get jobs,pods -n $Namespace" -ForegroundColor Red
}

Step "Logbook for this watch"
Invoke-Api GET "$Vigil/api/errands/$($errand.id)/checks?limit=10" |
    Select-Object at, status, value, rendered, message | Format-Table -AutoSize -Wrap

if ($Cleanup) {
    Step "Cleanup: delete the watch (and its CronJob)"
    & curl.exe -sS -o NUL -w "DELETE -> HTTP %{http_code}`n" -X DELETE "$Vigil/api/errands/$($errand.id)"
}
Write-Host "`nOpen the UI: $Vigil"
if (-not $found) { exit 1 }
