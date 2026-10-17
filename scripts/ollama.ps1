# Local-only Ollama status and explicit model download.
[CmdletBinding()]
param(
    [ValidateNotNullOrEmpty()][string]$Model,
    [switch]$Pull,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:11434"
if ($Pull -and [string]::IsNullOrWhiteSpace($Model)) {
    throw "-Pull requires -Model <name>."
}
if ($Model -and $Model -notmatch '^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$') {
    throw "Invalid Ollama model name."
}
if ($DryRun) {
    Write-Host "[dry-run] GET $base/api/tags"
    if ($Pull) { Write-Host "[dry-run] Would download Ollama model: $Model" }
    Write-Host "[dry-run] No network requests or downloads performed."
    return
}
try {
    $result = Invoke-RestMethod -Uri "$base/api/tags" -Method Get -TimeoutSec 5
} catch {
    throw "Ollama is not reachable at $base. Start Ollama and retry. $($_.Exception.Message)"
}
$models = @($result.models | ForEach-Object { $_.name })
Write-Host "Ollama reachable. Installed models: $($models.Count)"
foreach ($name in $models) { Write-Host " - $name" }
if ($Pull) {
    if (-not (Get-Command ollama.exe -ErrorAction SilentlyContinue)) {
        throw "ollama.exe not on PATH. Restart PowerShell or install Ollama first."
    }
    Write-Host "Downloading model '$Model'. This can require several GB of disk space."
    & ollama.exe pull $Model
    if ($LASTEXITCODE -ne 0) { throw "Ollama model download failed ($LASTEXITCODE)" }
    Write-Host "Model download completed: $Model"
}
