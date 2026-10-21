# Jet Studio 인수 프롬프트: 이미지·ComfyUI 기능 (JetAI에서 분리)

> 이 문서는 **Jet Studio 저장소의 Claude Code 세션**에 그대로 붙여 넣는 프롬프트입니다.
> JetAI(개발 토큰 절약 도구)에서는 이미지·ComfyUI 기능을 모두 제거했고, 이 문서가 그 기능의 전부입니다.
> 제거 직전 코드는 JetAI 저장소 커밋 `db80290`(브랜치 `claude/wonderful-cannon-4ajrxy`의 이력)에도 남아 있습니다: `git show db80290:workshop/comfyui.py`.

## 당신의 역할
Jet Studio는 **쇼츠(짧은 영상) 제작 프로젝트**입니다. 아래는 다른 프로젝트(JetAI)에서 먼저 만들어 둔 로컬 ComfyUI 연동 기능입니다. 이를 Jet Studio의 이미지/영상 생성 단계에 맞게 **이식·정리**하세요. JetAI 쪽 요구사항(토큰 절약, 채팅 UI)을 가져오지 마세요.

## 기능 요약 (JetAI에서 제거된 것)
1. **ComfyUI 제출** (`workshop/comfyui.py`): API 형식 워크플로 JSON을 `http://127.0.0.1:8188`(루프백만 허용)에 제출하고 `prompt_id`를 받는다. 제출 직후 prompt_id 형식을 검증(폴링·다운로드 전)한다.
2. **상태 조회/대기** (`workshop/comfyui_status.py`): history 조회, 완료 대기, 오류 분류.
3. **스모크 테스트/이미지 다운로드** (`scripts/comfyui_smoke.py`): 결과 PNG/JPEG/WebP 다운로드, 파일 시그니처·크기 한도 검사, SHA-256 기록, JSON 리포트, 기존 파일 덮어쓰기 금지, 기본은 미리보기이고 `--submit`일 때만 제출.
4. **Windows 스크립트**(`scripts/*comfyui*.ps1`): 소스 클론, 전용 venv, CUDA PyTorch 설치(cu126/cu128, 설치 후 `torch.cuda.is_available()` 확인), 체크포인트 다운로드(사용자가 준 HTTPS URL·파일명·SHA-256 필수, 기본 모델 없음, 라이선스 자동 수락 없음), 시작(루프백 8188), 상태 확인, GPU 진단. 모두 `-DryRun` 지원.
5. **작업 큐 연동**: `kind="image"` 작업, 승인(approval) 필수, 워커는 `--allow-comfyui`를 명시해야 실행, 완료 후 사람이 검토(review), 부작용이 있는 작업은 자동 재시도 금지.
6. **대시보드 갤러리**: `/image?id=&index=` 로 결과 이미지를 서빙(시그니처·크기 검사 후), 작업 상세에 썸네일 갤러리.
7. **진단**: ComfyUI 루프백 연결 확인(`adapters.comfyui_health`), 꺼져 있을 때의 한국어 안내 문구.

## 지켜야 할 안전 원칙 (그대로 유지)
- 루프백(127.0.0.1)만 허용, 외부 노출 금지. 워크플로는 커스텀 노드가 파일을 읽고 쓸 수 있으니 **신뢰한 워크플로만**.
- 모델 자동 다운로드·라이선스 자동 수락·관리자 권한 요청 금지. 체크포인트는 해시 검증.
- 이미지 출력 폴더는 절대 경로, 기존 파일 덮어쓰기 금지.
- 실제 GPU에서의 실행은 **한 번도 검증된 적 없음**: 모의 테스트를 하드웨어 검증이라고 쓰지 말 것.

## 해야 할 일
1. Jet Studio 저장소 구조를 읽고 이 기능이 들어갈 위치를 정한다(질문이 필요하면 먼저 묻는다).
2. 아래 부록의 코드를 가져와 이식하고 테스트를 함께 옮긴다. 모든 테스트는 임시 폴더·모의 서버만 사용.
3. 쇼츠 제작에 맞게 확장하는 것(영상 단계, 장면 큐 등)은 Jet Studio의 요구사항으로 별도 설계한다.
4. 실제 PC(GPU)에서 한 번 승인된 작업을 실행해 보고 결과를 사실대로 기록한다.

## 부록: 제거된 파일 전체

### `workshop/comfyui.py`

````python
"""Explicitly approved ComfyUI workflow submission (local-only).

This adapter submits API-format JSON to a trusted local ComfyUI instance.
It does not download models, install custom nodes, or wait for completion.
"""
import json
import secrets
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_WORKFLOW_BYTES = 2 * 1024 * 1024

def submit_workflow(workflow_path, *, enabled=False, base_url="http://127.0.0.1:8188", timeout=20):
    if not enabled:
        raise PermissionError("ComfyUI submission requires explicit opt-in")
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("ComfyUI URL must be a local HTTP origin")
    path = Path(workflow_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise ValueError("Workflow must be a JSON file smaller than 2 MiB")
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("Workflow must be a nonempty API-format JSON object")
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"Invalid API-format workflow node: {node_id}")
    body = json.dumps({"prompt": workflow, "client_id": secrets.token_hex(16)}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/prompt", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("prompt_id"), str):
        raise ValueError("ComfyUI did not return a prompt_id")
    return {"prompt_id": data["prompt_id"], "status": "submitted"}
````

### `workshop/comfyui_status.py`

````python
"""Read-only ComfyUI history polling and output metadata inspection."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

def _origin(base_url):
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Only local ComfyUI HTTP origins are allowed")
    return base_url.rstrip("/")

def history(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=10):
    if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 200:
        raise ValueError("Invalid prompt ID")
    url = _origin(base_url) + "/history/" + quote(prompt_id, safe="")
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("Unexpected ComfyUI history response")
    return data.get(prompt_id)

def inspect(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=10):
    item = history(prompt_id, base_url=base_url, timeout=timeout)
    if item is None:
        return {"prompt_id": prompt_id, "state": "pending", "outputs": []}
    status = item.get("status", {})
    messages = status.get("messages", [])
    if status.get("status_str") == "error" or any(
        isinstance(message, (list, tuple)) and message and message[0] == "execution_error"
        for message in messages
    ):
        return {"prompt_id": prompt_id, "state": "failed", "outputs": []}
    if not status.get("completed", False):
        return {"prompt_id": prompt_id, "state": "pending", "outputs": []}
    outputs = []
    for node_id, node in item.get("outputs", {}).items():
        for image in node.get("images", []):
            if isinstance(image, dict):
                outputs.append({"node_id": node_id, "filename": image.get("filename"),
                                "subfolder": image.get("subfolder", ""), "type": image.get("type")})
    return {"prompt_id": prompt_id, "state": "completed", "outputs": outputs}

def wait_for_completion(prompt_id, *, base_url="http://127.0.0.1:8188",
                        max_wait=300, interval=3):
    if max_wait <= 0 or interval <= 0:
        raise ValueError("Polling intervals must be positive")
    deadline = time.monotonic() + max_wait
    while True:
        result = inspect(prompt_id, base_url=base_url)
        if result["state"] in ("completed", "failed"):
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"prompt_id": prompt_id, "state": "timeout", "outputs": []}
        time.sleep(min(interval, remaining))
````

### `scripts/comfyui_smoke.py`

````python
"""Poll a local ComfyUI prompt until it finishes and validate output image metadata."""
import argparse
import hashlib
from pathlib import Path
from urllib.parse import urlencode
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from workshop.comfyui import submit_workflow


def wait_for_images(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=180):
    if not isinstance(prompt_id, str) or not prompt_id:
        raise ValueError("Invalid prompt ID")
    deadline = time.monotonic() + timeout
    url = base_url.rstrip("/") + "/history/" + quote(prompt_id, safe="")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                history = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ComfyUI history unavailable: {exc}") from exc
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(entry, dict):
            status = entry.get("status") or {}
            if status.get("status_str") == "error" or status.get("completed") is False and status.get("status_str") == "error":
                raise RuntimeError("ComfyUI reported a failed workflow")
            if status.get("completed") is True:
                images = []
                for node in (entry.get("outputs") or {}).values():
                    if not isinstance(node, dict):
                        continue
                    for item in node.get("images", []):
                        if isinstance(item, dict) and isinstance(item.get("filename"), str):
                            images.append({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "")})
                if not images:
                    raise RuntimeError("Workflow completed without image outputs")
                return images
        time.sleep(2)
    raise TimeoutError(f"ComfyUI prompt did not finish within {timeout}s")



MAX_IMAGE_BYTES = 64 * 1024 * 1024


def download_images(images, *, base_url, output_dir):
    folder = Path(output_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    results = []
    for index, item in enumerate(images, 1):
        name = item["filename"]
        subfolder = item.get("subfolder", "")
        kind = item.get("type", "output")
        if not isinstance(name, str) or not name or not isinstance(subfolder, str) or kind not in ("output", "temp"):
            raise ValueError("Invalid ComfyUI image metadata")
        url = base_url.rstrip("/") + "/view?" + urlencode({"filename": name, "subfolder": subfolder, "type": kind})
        with urllib.request.urlopen(url, timeout=30) as response:
            data = response.read(MAX_IMAGE_BYTES + 1)
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise ValueError("Image empty or larger than 64 MiB")
        if data.startswith(bytes.fromhex("89504e470d0a1a0a")):
            ext = ".png"
        elif data.startswith(bytes.fromhex("ffd8ff")):
            ext = ".jpg"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            ext = ".webp"
        else:
            raise ValueError("Unsupported or invalid image signature")
        digest = hashlib.sha256(data).hexdigest()
        target = folder / f"image-{index:03d}-{digest[:12]}{ext}"
        with target.open("xb") as output:
            output.write(data)
        results.append({"path": str(target), "bytes": len(data), "sha256": digest})
    return results


def save_report(report, path):
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    return target

def main():
    parser = argparse.ArgumentParser(description="Explicit local ComfyUI image generation smoke test")
    parser.add_argument("workflow", help="API-format ComfyUI workflow JSON file")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--output-dir", default="comfyui-smoke-output")
    parser.add_argument("--submit", action="store_true", help="Actually submit workflow")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 3600:
        parser.error("Invalid port or timeout")
    if not args.submit:
        print("[dry-run] Would submit workflow to localhost and wait for image output metadata.")
        return
    base = f"http://127.0.0.1:{args.port}"
    result = submit_workflow(args.workflow, enabled=True, base_url=base)
    print("Submitted prompt:", result["prompt_id"])
    images = wait_for_images(result["prompt_id"], base_url=base, timeout=args.timeout)
    files = download_images(images, base_url=base, output_dir=args.output_dir)
    report = {"prompt_id": result["prompt_id"], "status": "success", "images": files}
    report_path = save_report(report, Path(args.output_dir) / ("report-" + result["prompt_id"] + ".json"))
    print(json.dumps(report, indent=2))
    print("Report:", report_path)


if __name__ == "__main__":
    main()
````

### `scripts/install-comfyui.ps1`

````powershell
# Explicit opt-in ComfyUI source checkout on Windows.
# This prepares source files only; no Python environments, models or GPU libraries.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$upstream = "https://github.com/comfyanonymous/ComfyUI.git"
$full = [System.IO.Path]::GetFullPath($Destination)
if ($DryRun) {
    Write-Host "[dry-run] git clone -- $upstream $full"
    Write-Host "[dry-run] No checkout, dependency or model download performed."
    return
}
if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
    throw "Git for Windows is required. Install Git and retry."
}
if (Test-Path -LiteralPath $full) {
    throw "Destination already exists: $full. Refusing to overwrite or update it."
}
Write-Host "Cloning official ComfyUI source into $full"
& git.exe clone -- $upstream $full
if ($LASTEXITCODE -ne 0) { throw "ComfyUI clone failed ($LASTEXITCODE)" }
Write-Host "ComfyUI source downloaded. Python, PyTorch, CUDA, models and launch setup remain manual."
````

### `scripts/setup-comfyui-env.ps1`

````powershell
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
````

### `scripts/install-comfyui-torch.ps1`

````powershell
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
````

### `scripts/download-comfyui-model.ps1`

````powershell
# Download a user-selected ComfyUI checkpoint with mandatory SHA-256 verification.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Url,
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-fA-F0-9]{64}$')][string]$Sha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]*\.(safetensors|ckpt)$')][string]$FileName,
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$uri = [Uri]$Url
if ($uri.Scheme -ne "https" -or $uri.UserInfo -or $uri.Host -notin @("huggingface.co","civitai.com","www.civitai.com")) {
    throw "Only HTTPS downloads from huggingface.co or civitai.com are allowed."
}
$root = [System.IO.Path]::GetFullPath($Destination)
$folder = Join-Path $root "models/checkpoints"
$target = Join-Path $folder $FileName
if ($DryRun) {
    Write-Host "[dry-run] Would download $Url to $target and verify SHA-256 $Sha256"
    Write-Host "[dry-run] Existing files are never overwritten."
    return
}
if (-not (Test-Path -LiteralPath (Join-Path $root "main.py"))) {
    throw "ComfyUI checkout not found at $root."
}
if (Test-Path -LiteralPath $target) { throw "Target already exists: $target" }
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$temp = Join-Path $folder ([System.IO.Path]::GetRandomFileName() + ".download")
try {
    Invoke-WebRequest -Uri $uri -OutFile $temp -MaximumRedirection 5 -ErrorAction Stop
    $actual = (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash
    if ($actual -ne $Sha256) { throw "SHA-256 mismatch. Expected $Sha256; got $actual" }
    if (Test-Path -LiteralPath $target) { throw "Target appeared during download: $target" }
    [System.IO.File]::Move($temp, $target)
    Write-Host "Verified model downloaded: $target"
} finally {
    if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
}
````

### `scripts/start-comfyui.ps1`

````powershell
# Start an existing ComfyUI environment bound to localhost only.
[CmdletBinding()]
param(
    [string]$Destination = (Join-Path $HOME "ComfyUI"),
    [ValidateRange(1024,65535)][int]$Port = 8188,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$full = [System.IO.Path]::GetFullPath($Destination)
$python = Join-Path $full ".venv/Scripts/python.exe"
$main = Join-Path $full "main.py"
if ($DryRun) {
    Write-Host "[dry-run] Would start: $python $main --listen 127.0.0.1 --port $Port"
    Write-Host "[dry-run] No process started."
    return
}
if (-not (Test-Path -LiteralPath $python)) { throw "Missing ComfyUI venv: $python" }
if (-not (Test-Path -LiteralPath $main)) { throw "Missing ComfyUI entry point: $main" }
Write-Host "Starting ComfyUI on http://127.0.0.1:$Port"
Push-Location $full
try {
    & $python $main --listen 127.0.0.1 --port $Port
    if ($LASTEXITCODE -ne 0) { throw "ComfyUI exited with code $LASTEXITCODE" }
} finally {
    Pop-Location
}
````

### `scripts/comfyui-status.ps1`

````powershell
# Check a local ComfyUI server without installing models or starting processes.
[CmdletBinding()]
param(
    [ValidateRange(1,65535)][int]$Port = 8188,
    [switch]$DryRun
)
$ErrorActionPreference = "Stop"
$base = "http://127.0.0.1:$Port"
if ($DryRun) {
    Write-Host "[dry-run] Would query $base/system_stats"
    Write-Host "[dry-run] No network request or process start."
    return
}
try {
    $stats = Invoke-RestMethod -Uri "$base/system_stats" -Method Get -TimeoutSec 5
} catch {
    throw "ComfyUI unavailable at $base. Start ComfyUI manually and retry. $($_.Exception.Message)"
}
Write-Host "ComfyUI reachable: $base"
if ($stats.system -and $stats.system.comfyui_version) {
    Write-Host "Version: $($stats.system.comfyui_version)"
}
if ($stats.devices) {
    foreach ($device in $stats.devices) {
        Write-Host "Device: $($device.name)"
    }
}
````

### `scripts/comfyui-gpu.ps1`

````powershell
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
````

### `docs/comfyui.md`

````markdown
# ComfyUI API submission (experimental)

This is an opt-in local API integration. It requires ComfyUI already running at http://127.0.0.1:8188 and a workflow exported in **API format**, not the regular UI graph JSON.

```bash
python3 -m workshop.cli add image "Generate character sprite" --workflow /absolute/path/to/workflow-api.json
python3 -m workshop.worker --allow-comfyui
python3 -m workshop.cli list
```

**Important:** A returned prompt ID means ComfyUI **accepted** the workflow, not that image generation completed. The queue currently places accepted submissions in manual review; output polling, history lookup and artifact collection are not implemented. Do not approve the task until the output is inspected.

Only localhost HTTP origins are accepted. A ComfyUI workflow may run custom nodes and read/write local files, so use trusted workflows only. There is no model installation, GPU scheduler or unattended execution yet.

To run tests:

```bash
python3 -m unittest discover -s tests -v
```
````

### `docs/comfyui-status.md`

````markdown
# ComfyUI completion tracking

`workshop.comfyui_status.inspect(prompt_id)` checks ComfyUI history for a previously submitted prompt. It reports pending, failed or completed, and returns output image metadata (filename, subfolder, type) when available.

`wait_for_completion(prompt_id, max_wait=300, interval=3)` polls for terminal status, returning timeout if no terminal history appears.

**Worker integration:** The image worker now polls for completion for up to 300 seconds before moving a task into review. A failed or timed-out run is marked failed and never automatically replayed. No image files are downloaded or copied into a workspace. Only local loopback ComfyUI URLs are supported.

The API history response and output metadata should be treated as untrusted data. Do not interpret returned filenames as filesystem paths without validation.
````

### `tests/test_comfyui.py`

````python
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.comfyui import submit_workflow

class FakeResponse:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, *args):
        return b'{"prompt_id":"abc-123"}'

class ComfyTests(unittest.TestCase):
    def test_opt_in_required(self):
        with self.assertRaises(PermissionError):
            submit_workflow("unused.json")

    def test_reject_remote_url(self):
        with self.assertRaises(ValueError):
            submit_workflow("unused.json", enabled=True, base_url="https://example.com")

    def test_mocked_submission(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "workflow.json"
            workflow.write_text(json.dumps({"1":{"class_type":"EmptyLatentImage","inputs":{"width":512,"height":512,"batch_size":1}}}))
            with patch("workshop.comfyui.urllib.request.urlopen", return_value=FakeResponse()) as mocked:
                result = submit_workflow(workflow, enabled=True)
            self.assertEqual(result["prompt_id"], "abc-123")
            request = mocked.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:8188/prompt")
            self.assertEqual(request.get_method(), "POST")

if __name__ == "__main__":
    unittest.main()
````

### `tests/test_comfyui_smoke.py`

````python
import unittest
from unittest.mock import patch
from scripts.comfyui_smoke import wait_for_images, download_images, save_report
import tempfile
from pathlib import Path


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, *args):
        import json
        return json.dumps(self.payload).encode("utf-8")


class SmokeTests(unittest.TestCase):
    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_download_png_and_report(self, urlopen):
        class BinaryResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def read(self, *args):
                return bytes.fromhex("89504e470d0a1a0a") + b"test"
        urlopen.return_value = BinaryResponse()
        with tempfile.TemporaryDirectory() as temp:
            files = download_images([{"filename": "test.png", "type": "output"}],
                                    base_url="http://127.0.0.1:8188", output_dir=temp)
            self.assertEqual(len(files), 1)
            self.assertTrue(Path(files[0]["path"]).exists())
            report = save_report({"images": files}, Path(temp) / "report.json")
            self.assertTrue(report.exists())
            with self.assertRaises(FileExistsError):
                save_report({"images": files}, report)

    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_image_metadata(self, urlopen):
        urlopen.return_value = FakeResponse({
            "abc": {"status": {"completed": True}, "outputs": {
                "9": {"images": [{"filename": "test.png", "type": "output"}]}}}
        })
        self.assertEqual(wait_for_images("abc", timeout=1)[0]["filename"], "test.png")

    @patch("scripts.comfyui_smoke.urllib.request.urlopen")
    def test_completed_without_images_fails(self, urlopen):
        urlopen.return_value = FakeResponse({"abc": {"status": {"completed": True}, "outputs": {}}})
        with self.assertRaises(RuntimeError):
            wait_for_images("abc", timeout=1)


if __name__ == "__main__":
    unittest.main()
````

### `tests/test_comfyui_status.py`

````python
import unittest
from unittest.mock import patch
from workshop.comfyui_status import inspect, wait_for_completion

class StatusTests(unittest.TestCase):
    def test_pending(self):
        with patch("workshop.comfyui_status.history", return_value=None):
            self.assertEqual(inspect("abc")["state"], "pending")

    def test_completed_with_images(self):
        entry = {"status": {"completed": True, "status_str": "success"},
                 "outputs": {"4": {"images": [{"filename": "sprite.png", "subfolder": "", "type": "output"}]}}}
        with patch("workshop.comfyui_status.history", return_value=entry):
            result = inspect("abc")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["outputs"][0]["filename"], "sprite.png")

    def test_failed(self):
        entry = {"status": {"completed": False, "status_str": "error"}, "outputs": {}}
        with patch("workshop.comfyui_status.history", return_value=entry):
            self.assertEqual(inspect("abc")["state"], "failed")

    def test_wait(self):
        with patch("workshop.comfyui_status.inspect", side_effect=[
            {"state": "pending"}, {"state": "completed", "outputs": []}]):
            with patch("workshop.comfyui_status.time.sleep"):
                self.assertEqual(wait_for_completion("abc", max_wait=5, interval=0.01)["state"], "completed")
````

### `tests/test_image_worker.py`

````python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.queue import Queue
from workshop.worker import process_one

class ImageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_completed_image_enters_review(self):
        self.queue.add("image", "Sprite", {"workflow": "/tmp/workflow.json"})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "p1"}), patch(
                "workshop.worker.wait_for_completion", return_value={"state": "completed", "outputs": []}):
            result = process_one(self.queue, allow_comfyui=True)
        self.assertEqual(result["state"], "review")

    def test_timeout_fails_without_retry(self):
        self.queue.add("image", "Sprite", {"workflow": "/tmp/workflow.json"})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "p1"}), patch(
                "workshop.worker.wait_for_completion", return_value={"state": "timeout", "outputs": []}):
            result = process_one(self.queue, allow_comfyui=True)
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(self.queue.claim())
````

### workshop/dashboard.py 일부(작업 폼, 갤러리 헬퍼)

````python
            "done": " (완료)", "failed": " (실패 · 부수효과 확인 후 다시 추가)"}

def gallery_files(job):
    if not job or job["kind"] != "image" or job["state"] not in ("review", "done"):
        return []
    try:
        payload = json.loads(job.get("payload") or "{}")
        output_dir = payload.get("output_dir")
        if not isinstance(output_dir, str) or not Path(output_dir).is_absolute():
            return []
        root = Path(output_dir).resolve()
        files = json.loads(job.get("result") or "{}").get("files", [])
        if not isinstance(files, list):
            return []
        valid = []
        for item in files[:24]:
            if not isinstance(item, dict):
                continue
            path, digest = item.get("path"), item.get("sha256")
            if (isinstance(path, str) and Path(path).is_absolute()
                    and Path(path).suffix.lower() in (".png", ".jpg", ".webp")
                    and Path(path).resolve().parent == root
                    and Path(path).name.startswith("image-")
                    and isinstance(digest, str) and len(digest) == 64
                    and all(c in "0123456789abcdefABCDEF" for c in digest)):
                valid.append((Path(path), digest))
        return valid
````

### workshop/dashboard.py 일부(/image 라우트)

````python
                self._send(403, "Invalid Host", "text/plain")
                return
            if urlsplit(self.path).path == "/image":
                params = parse_qs(urlsplit(self.path).query)
                job_id = params.get("id", [""])[0]
                index_text = params.get("index", [""])[0]
                signature = params.get("sig", [""])[0]
                if not index_text.isdecimal() or len(index_text) > 3:
                    self._send(404, "Not found", "text/plain")
                    return
                index = int(index_text)
                if not hmac.compare_digest(signature, gallery_signature(token, job_id, index)):
                    self._send(403, "Forbidden", "text/plain")
                    return
                job = next((j for j in queue.list() if j["id"] == job_id), None)
                images = gallery_files(job)
                if index >= len(images):
                    self._send(404, "Not found", "text/plain")
                    return
                image_path, digest = images[index]
                try:
                    if not image_path.is_file() or image_path.stat().st_size > 64 * 1024 * 1024:
                        raise ValueError("Invalid image")
                    data = image_path.read_bytes()
                    if hashlib.sha256(data).hexdigest().lower() != digest.lower():
                        raise ValueError("Checksum mismatch")
                    ext = image_path.suffix.lower()
                    signatures = {".png": data.startswith(bytes.fromhex("89504e470d0a1a0a")),
                                  ".jpg": data.startswith(bytes.fromhex("ffd8ff")),
                                  ".webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP"}
                    if not signatures[ext]:
                        raise ValueError("Invalid image signature")
                except (OSError, ValueError):
                    self._send(404, "Image unavailable", "text/plain")
                    return
                self.send_response(200)
                self.send_header("Content-Type", {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}[ext])
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
````

### workshop/dashboard.py 일부(작업 상세 갤러리)

````python
                               '<button>Approve / 결과 승인</button></form>')
                detail = ""
                if job["kind"] == "image" and job["state"] in ("review", "done", "failed"):
                    try:
                        result = json.loads(job.get("result") or "{}")
                        files = result.get("files") or []
                        if isinstance(files, list) and files:
                            detail = "<small> · Generated files: " + str(len(files)) + "</small>"
                        if result.get("report_path"):
                            detail += "<small> · Report saved locally</small>"
                    except (ValueError, TypeError, AttributeError):
                        detail = ""
                gallery = ""
                for i, _ in enumerate(gallery_files(job)):
                    url = ("/image?id=" + quote(job["id"]) + "&index=" + str(i) +
                           "&sig=" + gallery_signature(token, job["id"], i))
                    gallery += '<a href="' + url + '"><img loading="lazy" alt="Generated image" src="' + url + '"></a>'
                if gallery:
                    detail += '<div class="gallery">' + gallery + '</div>'
````

### workshop/worker.py 일부(image 작업 실행)

````python
            pass

def process_one(queue, *, allow_opencode=False, allow_comfyui=False, max_running=None):
    if not (allow_opencode or allow_comfyui):
        raise PermissionError("Worker requires an explicit executor opt-in")
    # Claim only jobs with an enabled executor.
    enabled_kinds = []
    if allow_opencode:
        enabled_kinds.append("code")
    if allow_comfyui:
        enabled_kinds.append("image")
    job = queue.claim(kinds=enabled_kinds, max_running=max_running)
    if job is None:
        return None
    stop_beat = threading.Event()
    try:
        threading.Thread(target=_beat, args=(queue, job["id"], stop_beat), daemon=True).start()
        return _execute(queue, job, allow_opencode, allow_comfyui)
    finally:
        stop_beat.set()

def _execute(queue, job, allow_opencode, allow_comfyui):
    try:
        payload = json.loads(job["payload"])
        if job["kind"] == "code":
            if not allow_opencode:
                return _defer(queue, job)
            workspace = payload.get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise ValueError("Code job requires payload.workspace")
            result = run_opencode(job["title"], workspace, enabled=True)
        elif job["kind"] == "image":
            if not allow_comfyui:
                return _defer(queue, job)
            workflow = payload.get("workflow")
            if not isinstance(workflow, str) or not workflow:
                raise ValueError("Image job requires payload.workflow")
            if payload.get("save_outputs") is True:
                output_dir = payload.get("output_dir")
                if (not isinstance(output_dir, str) or not output_dir.strip()
                        or not Path(output_dir).is_absolute()):
                    raise ValueError("Image output saving requires an absolute payload.output_dir")
            submitted = submit_workflow(workflow, enabled=True)
            # Reject malformed identifiers before any polling, downloads or report writes.
            prompt_id = submitted.get("prompt_id") if isinstance(submitted, dict) else None
            if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
                raise ValueError("Invalid ComfyUI prompt identifier")
            if payload.get("save_outputs") is True:
                images = wait_for_images(submitted["prompt_id"], timeout=300)
                files = download_images(images, base_url="http://127.0.0.1:8188",
                                        output_dir=output_dir)
                report = {"prompt_id": submitted["prompt_id"], "state": "completed",
                          "files": files}
                report_path = save_report(report, Path(output_dir) /
                                          ("report-" + prompt_id + ".json"))
                result = {**report, "report_path": str(report_path)}
            else:
                result = wait_for_completion(submitted["prompt_id"], max_wait=300)
            if result["state"] != "completed":
                queue.finish(job["id"], result, success=False)
                return {"job_id": job["id"], "state": "failed", "result": result}
        else:
            raise ValueError("Unsupported executor kind: " + job["kind"])
````

### workshop/adapters.py 일부(comfyui_health)

````python
        timeout=timeout, check=False, env=opencode_env(binary),
    )
    return {"exit_code": completed.returncode,
            "stdout": completed.stdout[-16000:],
            "stderr": completed.stderr[-8000:]}

def comfyui_health(base_url="http://127.0.0.1:8188", timeout=3):
    """Read-only ComfyUI connectivity check, without submitting a workflow."""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password:
        raise ValueError("ComfyUI health check must target local HTTP loopback")
    with urllib.request.urlopen(base_url.rstrip("/") + "/system_stats", timeout=timeout) as response:
        return json.load(response)
````
