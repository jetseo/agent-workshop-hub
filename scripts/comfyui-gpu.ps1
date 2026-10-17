# Read-only NVIDIA/PyTorch diagnostics for a Windows ComfyUI checkout.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$full = [System.IO.Path]::GetFullPath($Destination)
$python = Join-Path $full ".venv/Scripts/python.exe"
if ($DryRun) {
    Write-Host "[dry-run] Would check nvidia-smi and $python for PyTorch/CUDA availability."
    Write-Host "[dry-run] No packages installed or modified."
    return
}
if (Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue) {
    Write-Host "NVIDIA driver GPU summary:"
    & nvidia-smi.exe --query-gpu=name,driver_version,memory.total --format=csv,noheader
    if ($LASTEXITCODE -ne 0) { Write-Warning "nvidia-smi returned an error." }
} else {
    Write-Warning "nvidia-smi.exe not found. NVIDIA driver/GPU is not confirmed."
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "ComfyUI Python venv not found: $python. Run -SetupComfyUIEnv first."
}
$probe = @'
import json
try:
    import torch
except ImportError:
    print(json.dumps({"torch_installed": False}))
else:
    result = {
        "torch_installed": True,
        "torch_version": torch.__version__,
        "torch_cuda_build": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "device_count": torch.cuda.device_count() if torch.cuda.is_available() else 0,
    }
    if result["cuda_available"]:
        result["devices"] = [torch.cuda.get_device_name(i) for i in range(result["device_count"])]
    print(json.dumps(result))
'@
$probe | & $python -c "import sys; exec(sys.stdin.read())"
if ($LASTEXITCODE -ne 0) { throw "PyTorch probe failed ($LASTEXITCODE)" }
Write-Host "Diagnostics complete. No CUDA/PyTorch installation or changes performed."
