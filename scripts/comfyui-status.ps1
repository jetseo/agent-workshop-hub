# Check a local ComfyUI server without installing models or starting processes.
[CmdletBinding()]
param(
    [ValidateRange(1,65535)][int]$Port = 8188,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:$Port"
if ($DryRun) {
    Write-Host "[dry-run] Would query $base/system_stats"
    Write-Host "[dry-run] No network request or process start."
    return
}
try {
    $stats = Invoke-RestMethod -Uri "$base/system_stats" -Method Get -TimeoutSec 5
} catch {
    throw "ComfyUI unavailable at $base. Start ComfyUI manually and retry. $($_.Exception.Message)"
}
Write-Host "ComfyUI reachable: $base"
if ($stats.system -and $stats.system.comfyui_version) {
    Write-Host "Version: $($stats.system.comfyui_version)"
}
if ($stats.devices) {
    foreach ($device in $stats.devices) {
        Write-Host "Device: $($device.name)"
    }
}
