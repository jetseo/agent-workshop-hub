# Agent Workshop Hub - Windows launcher (WSL2)
# Run after WSL Ubuntu and prerequisites are configured.
[CmdletBinding()]
param(
    [switch]$AllowOpenCode,
    [switch]$AllowComfyUI,
    [int]$Port = 8787
)
$ErrorActionPreference = "Stop"
if ($Port -lt 1024 -or $Port -gt 65535) { throw "Port must be between 1024 and 65535" }
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) { throw "WSL is required. Run install.ps1 first." }
$launcher = Join-Path $PSScriptRoot "scripts/start.sh"
if (-not (Test-Path $launcher)) { throw "Missing scripts/start.sh" }
$path = (Resolve-Path $launcher).Path
$wslPath = (& wsl.exe -d Ubuntu-24.04 -- wslpath -a $path).Trim()
if ($LASTEXITCODE -ne 0 -or -not $wslPath) { throw "Could not resolve project path inside WSL" }
$argsList = @("-d", "Ubuntu-24.04", "--", "bash", $wslPath, "--port=$Port")
if ($AllowOpenCode) { $argsList += "--allow-opencode" }
if ($AllowComfyUI) { $argsList += "--allow-comfyui" }
Write-Host "Starting local Agent Workshop Hub (Ctrl+C to stop)."
& wsl.exe @argsList
if ($LASTEXITCODE -ne 0) { throw "Hub exited with code $LASTEXITCODE" }
