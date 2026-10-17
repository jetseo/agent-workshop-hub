# Agent Workshop Hub - Windows preflight and opt-in Ubuntu prerequisite setup.
[CmdletBinding()]
param(
    [switch]$InstallWSL,
    [switch]$RunUbuntuBootstrap,
    [switch]$InstallOpenCode,
    [ValidateNotNullOrEmpty()][string]$Distro = "Ubuntu-24.04",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
if ($PSVersionTable.PSEdition -eq "Core" -and -not $IsWindows) {
    throw "Windows required"
}
Write-Host "Agent Workshop Hub | Windows preflight"
if ($DryRun) {
    Write-Host "[dry-run] WSL distro: $Distro"
    if ($InstallWSL) { Write-Host "[dry-run] Would install WSL distro if absent: $Distro" }
    if ($InstallOpenCode) { Write-Host "[dry-run] Would run scripts/install-opencode.sh in $Distro" }
    if ($RunUbuntuBootstrap) {
        Write-Host "[dry-run] Would install Ubuntu prerequisites: git curl ca-certificates python3 python3-venv python3-pip jq"
    }
    Write-Host "[dry-run] No system changes performed."
    return
}
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw "wsl.exe unavailable. Enable WSL in Windows first, then rerun."
}
$raw = @(& wsl.exe --list --quiet 2>&1)
if ($LASTEXITCODE -ne 0) { throw "Unable to query WSL distributions" }
$names = @($raw | ForEach-Object { ($_ -replace [char]0, "").Trim() })
if ($Distro -notin $names) {
    if (-not $InstallWSL) {
        throw "WSL distro '$Distro' is not installed. Rerun with -InstallWSL in elevated PowerShell."
    }
    Write-Host "Installing WSL distro $Distro. Administrator approval or restart may be required."
    & wsl.exe --install -d $Distro
    if ($LASTEXITCODE -ne 0) { throw "WSL distro installation failed ($LASTEXITCODE)" }
    Write-Host "Complete the Ubuntu first-run user setup and rerun install.ps1."
    return
}
Write-Host "WSL distro ready: $Distro"
if (-not ($RunUbuntuBootstrap -or $InstallOpenCode)) {
    Write-Host "Preflight complete. Use -RunUbuntuBootstrap and/or -InstallOpenCode."
    return
}
if ($RunUbuntuBootstrap) {
    $script = Join-Path $PSScriptRoot "scripts/bootstrap.sh"
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing scripts/bootstrap.sh" }
    $bootstrap = (Get-Content -Raw -LiteralPath $script) -replace "`r`n", "`n"
    $bootstrap | & wsl.exe -d $Distro -- bash -s
    if ($LASTEXITCODE -ne 0) { throw "Ubuntu bootstrap failed ($LASTEXITCODE)" }
    Write-Host "Base prerequisites installed."
}
if ($InstallOpenCode) {
    $script = Join-Path $PSScriptRoot "scripts/install-opencode.sh"
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing scripts/install-opencode.sh" }
    $installer = (Get-Content -Raw -LiteralPath $script) -replace "`r`n", "`n"
    $installer | & wsl.exe -d $Distro -- bash -s
    if ($LASTEXITCODE -ne 0) { throw "OpenCode installation failed ($LASTEXITCODE)" }
    Write-Host "OpenCode installation step complete."
}
