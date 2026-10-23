# JetAI - build a portable Windows package (zip): app + the official embeddable Python, no installer, no admin rights.
# Unzip anywhere and run "JetAI.cmd". Nothing is written outside the unzipped folder except the usual data
# folder (~/.agent-workshop), and nothing is downloaded when the package runs.
[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$PythonVersion = "3.12.8",
    [string]$OutDir = "",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if (-not $OutDir) { $OutDir = Join-Path $PSScriptRoot "../dist" }          # a $PSScriptRoot default inside param() is empty on some PowerShell 5.1 hosts
$repo = Split-Path -Parent $PSScriptRoot
$url = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
$name = "JetAI-portable"
if ($DryRun) {
    Write-Host "[dry-run] Download (HTTPS, python.org only): $url"
    Write-Host "[dry-run] Package folder: $name (app files + python/ + 'JetAI.cmd'), zip written to $OutDir"
    Write-Host "[dry-run] Nothing was downloaded or created."
    return
}
$work = Join-Path ([IO.Path]::GetTempPath()) ("jetai-build-" + [guid]::NewGuid().ToString("N"))
$stage = Join-Path $work $name
New-Item -ItemType Directory -Path $stage -Force | Out-Null
try {
    $embed = Join-Path $work "python-embed.zip"
    Invoke-WebRequest -Uri $url -OutFile $embed -UseBasicParsing
    $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $embed).Hash.ToLower()
    Expand-Archive -LiteralPath $embed -DestinationPath (Join-Path $stage "python")
    # The embeddable Python ignores PYTHONPATH and the current directory: add the package root explicitly.
    $pth = Get-ChildItem (Join-Path $stage "python") -Filter "python*._pth" | Select-Object -First 1
    if (-not $pth) { throw "The embeddable Python has no ._pth file; the layout changed." }
    $lines = @(Get-Content -LiteralPath $pth.FullName | Where-Object { $_ -and $_ -notmatch '^\s*#\s*import site' })
    $lines += ".."
    Set-Content -LiteralPath $pth.FullName -Value $lines -Encoding ASCII
    Copy-Item -Recurse -LiteralPath (Join-Path $repo "workshop") -Destination (Join-Path $stage "workshop")
    Get-ChildItem (Join-Path $stage "workshop") -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    foreach ($file in @("jetai.ps1", "README.md", "LICENSE")) {
        if (Test-Path -LiteralPath (Join-Path $repo $file)) { Copy-Item -LiteralPath (Join-Path $repo $file) -Destination $stage }
    }
    Copy-Item -Recurse -LiteralPath (Join-Path $repo "docs") -Destination (Join-Path $stage "docs")
    # Launcher exe (built with the C# compiler that ships with Windows) and the icon; falls back to JetAI.cmd only.
    & (Join-Path $PSScriptRoot "build-exe.ps1") -OutDir $stage | Out-Null
    Set-Content -LiteralPath (Join-Path $stage "VERSION.txt") -Value (Get-Date -Format "yyyy.MM.dd") -Encoding ASCII
    $cmd = "@echo off`r`ncd /d `"%~dp0`"`r`npowershell.exe -NoProfile -ExecutionPolicy Bypass -File `"%~dp0jetai.ps1`" %*`r`n"
    Set-Content -LiteralPath (Join-Path $stage "JetAI.cmd") -Value $cmd -Encoding ASCII -NoNewline
    $manifest = "JetAI portable package`r`nPython: $PythonVersion embeddable (amd64), downloaded over HTTPS from python.org`r`nPython zip SHA256: $hash`r`nBuilt: $((Get-Date).ToUniversalTime().ToString('yyyy-MM-dd HH:mm')) UTC`r`n"
    Set-Content -LiteralPath (Join-Path $stage "PACKAGE.txt") -Value $manifest -Encoding ASCII
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
    $zip = Join-Path (Resolve-Path $OutDir).Path "$name.zip"
    if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
    Compress-Archive -Path $stage -DestinationPath $zip
    Write-Host "Built: $zip"
    Write-Host "Python zip SHA256: $hash"
} finally {
    Remove-Item -Recurse -Force -LiteralPath $work -ErrorAction SilentlyContinue
}
