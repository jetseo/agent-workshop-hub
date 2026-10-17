# Start an existing ComfyUI environment bound to localhost only.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [ValidateRange(1024,65535)][int]$Port = 8188,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$full = [System.IO.Path]::GetFullPath($Destination)
$python = Join-Path $full ".venv/Scripts/python.exe"
$main = Join-Path $full "main.py"
if ($DryRun) {
    Write-Host "[dry-run] Would start: $python $main --listen 127.0.0.1 --port $Port"
    Write-Host "[dry-run] No process started."
    return
}
if (-not (Test-Path -LiteralPath $python)) { throw "Missing ComfyUI venv: $python" }
if (-not (Test-Path -LiteralPath $main)) { throw "Missing ComfyUI entry point: $main" }
Write-Host "Starting ComfyUI on http://127.0.0.1:$Port"
Push-Location $full
try {
    & $python $main --listen 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
