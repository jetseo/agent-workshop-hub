# Agent Workshop Hub - Windows launcher (WSL2)
[CmdletBinding()]
param(
    [switch]$AllowOpenCode,
    [switch]$AllowComfyUI,
    [ValidateRange(1024,65535)][int]$Port = 8787,
    [ValidateNotNullOrEmpty()][string]$Distro = "Ubuntu-24.04",
    [switch]$NoBrowser,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$launcher = Join-Path $PSScriptRoot "scripts/start.sh"
if (-not (Test-Path -LiteralPath $launcher)) { throw "Missing scripts/start.sh" }
$flags = @("--port=$Port")
if ($AllowOpenCode) { $flags += "--allow-opencode" }
if ($AllowComfyUI) { $flags += "--allow-comfyui" }
if ($DryRun) {
    Write-Host "[dry-run] WSL distro: $Distro"
    Write-Host "[dry-run] Launcher: $launcher"
    Write-Host "[dry-run] Arguments: $($flags -join ' ')"
    if (-not $NoBrowser) { Write-Host "[dry-run] Would open http://127.0.0.1:$Port/setup in the default browser (use -NoBrowser to skip)" }
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
    # Open the setup page shortly after the server starts; failure to open a browser is not fatal.
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 4; Start-Process $u } -ArgumentList "http://127.0.0.1:$Port/setup" | Out-Null
}
Write-Host "Starting local Agent Workshop Hub (Ctrl+C to stop). Setup page: http://127.0.0.1:$Port/setup"
& wsl.exe @argsList
if ($LASTEXITCODE -ne 0) { throw "Hub exited with code $LASTEXITCODE" }
