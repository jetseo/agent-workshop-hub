# JetAI Hub - Windows launcher (WSL2)
[CmdletBinding()]
param(
    [switch]$AllowOpenCode,
    [switch]$AllowComfyUI,
    [ValidateRange(1024,65535)][int]$Port = 8787,
    [ValidateNotNullOrEmpty()][string]$Distro = "Ubuntu-24.04",
    [switch]$Terminal,
    [switch]$NoBrowser,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "scripts/start.sh"
if (-not (Test-Path -LiteralPath $launcher)) { throw "Missing scripts/start.sh" }
$flags = @("--port=$Port")
if ($AllowOpenCode) { $flags += "--allow-opencode" }
if ($AllowComfyUI) { $flags += "--allow-comfyui" }
if ($Terminal) { $flags += "--enable-terminal" }
if ($DryRun) {
    Write-Host "[dry-run] WSL distro: $Distro"
    Write-Host "[dry-run] Launcher: $launcher"
    Write-Host "[dry-run] Arguments: $($flags -join ' ')"
    if (-not $NoBrowser) { Write-Host "[dry-run] Would open an app-style window at http://127.0.0.1:$Port/app (Edge --app, else the default browser; -NoBrowser to skip)" }
    if ($Terminal) { Write-Host "[dry-run] Terminal tab enabled: opens with a private start-up key; runs a shell as your WSL user" }
    Write-Host "[dry-run] No WSL process or AI executor started."
    return
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe was not found. Install WSL2 first: review with .\install.ps1 -DryRun, then run .\install.ps1 -InstallWSL from an administrator PowerShell (a reboot may be required)."
}
$distros = @(& wsl.exe --list --quiet 2>&1)
if ($LASTEXITCODE -ne 0) { throw "Unable to query WSL distributions (wsl.exe exit code $LASTEXITCODE). Run 'wsl --status' to check that WSL2 is enabled." }
# wsl.exe output may contain NUL characters in Windows PowerShell.
$names = @($distros | ForEach-Object { ($_ -replace [char]0, "").Trim() })
if ($Distro -notin $names) {
    throw "WSL distro '$Distro' not found. Installed: $($names -join ', '). Re-run with -Distro <name> or install it with 'wsl --install -d $Distro'."
}
$path = (Resolve-Path -LiteralPath $launcher).Path
$wslPath = (& wsl.exe -d $Distro -- wslpath -a $path).Trim()
if ($LASTEXITCODE -ne 0 -or -not $wslPath) {
    throw "Could not convert '$path' to a WSL path in distro '$Distro'. Check that the distro starts ('wsl -d $Distro -- echo ok')."
}
$argsList = @("-d", $Distro, "--", "bash", $wslPath) + $flags
if (-not $NoBrowser) {
    # Wait for the server, then open an app-style window (no tabs or address bar). Failing to open a
    # window is not fatal: the URLs are also printed by the server.
    $opener = {
        param($port, $distro, $withTerminal)
        $base = "http://127.0.0.1:$port"
        $deadline = (Get-Date).AddSeconds(45)
        while ((Get-Date) -lt $deadline) {
            try { Invoke-WebRequest -Uri "$base/api/jobs" -UseBasicParsing -TimeoutSec 2 | Out-Null; break }
            catch { Start-Sleep -Milliseconds 700 }
        }
        $url = "$base/app"
        if ($withTerminal) {
            $key = ((& wsl.exe -d $distro -- bash -lc 'cat ~/.agent-workshop/terminal.key' 2>$null) | Out-String) -replace [char]0, ""
            $key = $key.Trim()
            if ($key) { $url = "$base/app?key=$key" }
        }
        try { Start-Process msedge -ArgumentList "--app=$url" -ErrorAction Stop }
        catch { Start-Process $url }
    }
    Start-Job -ScriptBlock $opener -ArgumentList $Port, $Distro, ([bool]$Terminal) | Out-Null
}
Write-Host "Starting local JetAI Hub (Ctrl+C to stop). App window: http://127.0.0.1:$Port/app"
& wsl.exe @argsList
if ($LASTEXITCODE -ne 0) { throw "Hub exited with code $LASTEXITCODE" }
