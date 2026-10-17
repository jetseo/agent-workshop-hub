# Explicit opt-in ComfyUI source checkout on Windows.
# This prepares source files only; no Python environments, models or GPU libraries.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$upstream = "https://github.com/comfyanonymous/ComfyUI.git"
$full = [System.IO.Path]::GetFullPath($Destination)
if ($DryRun) {
    Write-Host "[dry-run] git clone -- $upstream $full"
    Write-Host "[dry-run] No checkout, dependency or model download performed."
    return
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw "Git for Windows is required. Install Git and retry."
}
if (Test-Path -LiteralPath $full) {
    throw "Destination already exists: $full. Refusing to overwrite or update it."
}
Write-Host "Cloning official ComfyUI source into $full"
& git.exe clone -- $upstream $full
if ($LASTEXITCODE -ne 0) { throw "ComfyUI clone failed ($LASTEXITCODE)" }
Write-Host "ComfyUI source downloaded. Python, PyTorch, CUDA, models and launch setup remain manual."
