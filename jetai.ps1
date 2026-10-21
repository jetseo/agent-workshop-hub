# JetAI Hub - native Windows launcher (no WSL needed): chat, onboarding, usage, digest, boards of text steps.
# The WSL terminal tab, OpenCode and git-worktree code steps still need WSL; use start.ps1 for those.
[CmdletBinding()]
param(
    [ValidateRange(1024,65535)][int]$Port = 8787,
    [switch]$NoBrowser,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot "workshop/dashboard.py"))) { throw "Missing workshop/dashboard.py next to jetai.ps1" }

function Find-Python {
    # Try the py launcher first, then python/python3. Each candidate must really run and be 3.10+:
    # the Microsoft Store "python.exe" stub exists on PATH but only opens the Store.
    $candidates = @(@("py", "-3"), @("python"), @("python3"))
    foreach ($candidate in $candidates) {
        $cmd = Get-Command $candidate[0] -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $extra = @(); if ($candidate.Count -gt 1) { $extra = $candidate[1..($candidate.Count - 1)] }
        try {
            $out = & $cmd.Source @extra -c "import sys; print(sys.version_info[0]*100+sys.version_info[1])" 2>$null
            if ($LASTEXITCODE -eq 0 -and [int]("$out".Trim()) -ge 310) { return @{ Exe = $cmd.Source; Extra = $extra } }
        } catch { }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    throw "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), or with: winget install Python.Python.3.12 - then run this again."
}
$serverArgs = @($python.Extra) + @("-m", "workshop.dashboard", "--port", "$Port")
if ($DryRun) {
    Write-Host "[dry-run] Python: $($python.Exe) $($python.Extra -join ' ')"
    Write-Host "[dry-run] Command: python $($serverArgs -join ' ') (working directory: $PSScriptRoot)"
    if (-not $NoBrowser) { Write-Host "[dry-run] Would open an app-style window at http://127.0.0.1:$Port/app (Edge --app, else the default browser)" }
    Write-Host "[dry-run] Data folder: $(Join-Path $HOME '.agent-workshop') (keys, chats, usage; unchanged)"
    Write-Host "[dry-run] No WSL process, AI executor or network call started."
    return
}
$busy = $null
try { $busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction Stop } catch { }
if ($busy) { throw "Port $Port is already in use. Close the other JetAI Hub window, or run: .\jetai.ps1 -Port <another port>" }
if (-not $NoBrowser) {
    $opener = {
        param($port)
        $base = "http://127.0.0.1:$port"
        $deadline = (Get-Date).AddSeconds(30)
        while ((Get-Date) -lt $deadline) {
            try { Invoke-WebRequest -Uri "$base/app" -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
            catch { Start-Sleep -Milliseconds 500 }
        }
        try { Start-Process msedge -ArgumentList "--app=$base/app" -ErrorAction Stop }
        catch { Start-Process "$base/app" }
    }
    Start-Job -ScriptBlock $opener -ArgumentList $Port | Out-Null
}
Write-Host "Starting local JetAI Hub (Ctrl+C or closing this window stops it). App window: http://127.0.0.1:$Port/app"
Push-Location $PSScriptRoot
try {
    & $python.Exe @serverArgs
    if ($LASTEXITCODE -ne 0) { throw "JetAI Hub exited with code $LASTEXITCODE" }
} finally { Pop-Location }
