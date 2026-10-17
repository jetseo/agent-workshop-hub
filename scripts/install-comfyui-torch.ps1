# Explicit PyTorch CUDA wheel installation for an existing ComfyUI venv.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [ValidateSet("cu126","cu128")][string]$CudaWheel = "cu128",
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$full = [System.IO.Path]::GetFullPath($Destination)
$python = Join-Path $full ".venv/Scripts/python.exe"
$index = "https://download.pytorch.org/whl/$CudaWheel"
if ($DryRun) {
    Write-Host "[dry-run] Would install torch torchvision torchaudio from $index into $python"
    Write-Host "[dry-run] No downloads or package modifications."
    return
}
if (-not (Test-Path -LiteralPath $python)) { throw "Missing ComfyUI Python venv: $python" }
if (-not (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue)) {
    throw "NVIDIA driver not detected via nvidia-smi.exe; refusing CUDA installation."
}
& nvidia-smi.exe --query-gpu=name --format=csv,noheader
if ($LASTEXITCODE -ne 0) { throw "NVIDIA driver probe failed" }
Write-Warning "CUDA wheel $CudaWheel must be compatible with your GPU driver. Large downloads may occur."
& $python -m pip install torch torchvision torchaudio --index-url $index
if ($LASTEXITCODE -ne 0) { throw "PyTorch CUDA package installation failed ($LASTEXITCODE)" }
& $python -c "import torch; assert torch.cuda.is_available(), 'CUDA unavailable'; print(torch.__version__, torch.cuda.get_device_name(0))"
if ($LASTEXITCODE -ne 0) { throw "PyTorch installed, but CUDA is not available. Check driver and wheel compatibility." }
Write-Host "PyTorch CUDA runtime check passed."
