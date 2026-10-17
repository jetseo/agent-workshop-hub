# Agent Workshop Hub - Windows preflight and opt-in Ubuntu prerequisite setup.
[CmdletBinding()]
param(
    [switch]$InstallWSL,
    [switch]$RunUbuntuBootstrap,
    [switch]$InstallOpenCode,
    [switch]$InstallNode,
    [switch]$UseNvmFallback,
    [switch]$InstallOllama,
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
    if ($InstallOllama) { Write-Host "[dry-run] Would install Ollama on Windows using winget --id Ollama.Ollama --exact" }
    if ($InstallWSL) { Write-Host "[dry-run] Would install WSL distro if absent: $Distro" }
    if ($InstallOpenCode -or $InstallNode) { Write-Host "[dry-run] Would check/install Node.js prerequisite in $Distro" }
    if ($UseNvmFallback) { Write-Host "[dry-run] Would allow nvm fallback if Ubuntu Node.js is too old" }
    if ($InstallOpenCode) { Write-Host "[dry-run] Would run scripts/install-opencode.sh in $Distro" }
    if ($RunUbuntuBootstrap) {
        Write-Host "[dry-run] Would install Ubuntu prerequisites: git curl ca-certificates python3 python3-venv python3-pip jq"
    }
    Write-Host "[dry-run] No system changes performed."
    return
}
if ($InstallOllama) {
    if (-not (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
        throw "winget.exe unavailable. Install Microsoft App Installer, then retry -InstallOllama."
    }
    Write-Host "Installing Ollama on Windows through winget (explicit opt-in)."
    & winget.exe install --id Ollama.Ollama --exact --source winget --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { throw "Ollama winget installation failed ($LASTEXITCODE)" }
    Write-Host "Ollama install command finished. Verify service/model access separately."
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
if (-not ($RunUbuntuBootstrap -or $InstallOpenCode -or $InstallNode)) {
    Write-Host "Preflight complete. Use -RunUbuntuBootstrap, -InstallNode and/or -InstallOpenCode."
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
if ($InstallOpenCode -or $InstallNode) {
    $script = Join-Path $PSScriptRoot "scripts/install-node.sh"
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing scripts/install-node.sh" }
    $installer = (Get-Content -Raw -LiteralPath $script) -replace "`r`n", "`n"
    if ($UseNvmFallback) {
        $installer | & wsl.exe -d $Distro -- bash -s -- --use-nvm
    } else {
        $installer | & wsl.exe -d $Distro -- bash -s
    }
    if ($LASTEXITCODE -ne 0) { throw "Node.js prerequisite failed ($LASTEXITCODE)" }
}
if ($InstallOpenCode) {
    $script = Join-Path $PSScriptRoot "scripts/install-opencode.sh"
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing scripts/install-opencode.sh" }
    $installer = (Get-Content -Raw -LiteralPath $script) -replace "`r`n", "`n"
    $installer | & wsl.exe -d $Distro -- bash -s
    if ($LASTEXITCODE -ne 0) { throw "OpenCode installation failed ($LASTEXITCODE)" }
    Write-Host "OpenCode installation step complete."
}
