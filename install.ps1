# Agent Workshop Hub - Windows bootstrap
# Run in PowerShell. Use -InstallWSL to opt into OS changes.
[CmdletBinding()]
param(
    [switch]$InstallWSL,
    [switch]$RunUbuntuBootstrap,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
Write-Host "Agent Workshop Hub | Windows preflight"
if (-not $IsWindows -and $PSVersionTable.PSEdition -eq 'Core') { throw "Windows required" }
$wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $wsl) {
    if (-not $InstallWSL) { Write-Warning "WSL unavailable. Rerun with -InstallWSL in an elevated PowerShell."; exit 1 }
    if ($DryRun) { Write-Host "[dry-run] wsl --install -d Ubuntu-24.04"; exit 0 }
    wsl.exe --install -d Ubuntu-24.04
    Write-Host "Restart Windows if prompted, finish Ubuntu first-run setup, then rerun."
    exit 0
}
wsl.exe --list --verbose
if ($LASTEXITCODE -ne 0) { throw "Unable to query WSL distributions" }
if (-not $RunUbuntuBootstrap) {
    Write-Host "Preflight complete. Rerun with -RunUbuntuBootstrap to configure Ubuntu prerequisites."
    exit 0
}
if ($DryRun) { Write-Host "[dry-run] Ubuntu bootstrap (git, curl, python3, venv)"; exit 0 }
$script = Join-Path $PSScriptRoot "scripts/bootstrap.sh"
if (-not (Test-Path $script)) { throw "Missing scripts/bootstrap.sh" }
# Pass script via stdin to avoid Windows/WSL path translation problems.
Get-Content -Raw -Path $script | wsl.exe -d Ubuntu-24.04 -- bash -s
if ($LASTEXITCODE -ne 0) { throw "Ubuntu bootstrap failed with exit code $LASTEXITCODE" }
Write-Host "Bootstrap completed. See docs/setup.md for optional runtimes."
