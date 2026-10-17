# Explicit opt-in ComfyUI Python environment setup on Windows.
# CPU and GPU package selection is intentionally left to the operator.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$InstallRequirements,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$full = [System.IO.Path]::GetFullPath($Destination)
$venv = Join-Path $full ".venv"
$python = Join-Path $venv "Scripts/python.exe"
if ($DryRun) {
    Write-Host "[dry-run] Verify ComfyUI checkout at $full"
    Write-Host "[dry-run] py -3.11 -m venv $venv"
    Write-Host "[dry-run] $python -m pip install --upgrade pip"
    if ($InstallRequirements) {
        Write-Host "[dry-run] $python -m pip install -r $full/requirements.txt"
        Write-Host "[dry-run] Warning: PyTorch backend compatibility is not verified."
    }
    Write-Host "[dry-run] No files, packages or models modified."
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $full "main.py"))) {
    throw "ComfyUI main.py not found at $full. Clone ComfyUI first."
}
if (-not (Test-Path -LiteralPath (Join-Path $full "requirements.txt"))) {
    throw "ComfyUI requirements.txt missing at $full."
}
if (-not (Test-Path -LiteralPath $python)) {
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) {
        throw "Python launcher py.exe not found. Install Python 3.11 first."
    }
    & py.exe -3.11 -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw "Python virtual environment creation failed ($LASTEXITCODE)" }
}
& $python -m pip --version
if ($LASTEXITCODE -ne 0) { throw "ComfyUI virtual environment pip unavailable" }
if ($InstallRequirements) {
    Write-Warning "Installing upstream requirements. This may download large PyTorch packages; GPU support is not guaranteed."
    & $python -m pip install -r (Join-Path $full "requirements.txt")
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI requirements installation failed ($LASTEXITCODE)" }
}
Write-Host "ComfyUI Python environment ready at $venv"
Write-Host "GPU/PyTorch compatibility and model downloads require separate validation."
