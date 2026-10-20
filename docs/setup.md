# Installation and safety

## Windows preflight

In PowerShell from the repository root:

```powershell
.\install.ps1 -DryRun
.\install.ps1
```

If the requested Ubuntu distribution is missing, use an elevated PowerShell
and explicitly opt in:

```powershell
.\install.ps1 -InstallWSL
```

Reboot if requested and complete the Ubuntu user setup. The default distro
is `Ubuntu-24.04`; pass `-Distro <name>` to select another installed or
installable distribution. Dry-run never invokes WSL or installs packages.

To install Ubuntu prerequisites and then attempt the opt-in OpenCode setup
from Windows PowerShell:

```powershell
.\\install.ps1 -DryRun -RunUbuntuBootstrap -InstallOpenCode
.\\install.ps1 -RunUbuntuBootstrap -InstallOpenCode
```

**Node.js:** `-InstallOpenCode` now runs the opt-in Node.js prerequisite installer first. It checks for Node.js 20+ and npm, and attempts installation from Ubuntu `apt` when missing. Some Ubuntu releases provide an older Node.js through `apt`; the installer now checks the candidate version before installing and stops with a clear error if it is too old. In that case install a supported Node.js release separately, or opt into the nvm fallback: `./install.ps1 -RunUbuntuBootstrap -InstallOpenCode -UseNvmFallback`. This clones nvm v0.40.3 from its upstream GitHub repository and installs Node.js 22 under the current WSL user. The installer and launcher source `~/.nvm/nvm.sh` in noninteractive shells. Existing compatible Node.js installations are preserved.

To install only base Linux prerequisites:

```powershell
.\install.ps1 -RunUbuntuBootstrap
```

Or within Ubuntu:

```bash
bash scripts/bootstrap.sh --dry-run
bash scripts/bootstrap.sh
```

## Windows launcher verification

From the repository root, validate the launcher without starting WSL or an AI
executor:

```powershell
.\\start.ps1 -DryRun
.\\start.ps1 -DryRun -Distro Ubuntu-24.04 -Port 8787
```

Start the dashboard only after configuring WSL and base prerequisites:

```powershell
.\\start.ps1 -Distro Ubuntu-24.04
```

To enable an executor, review its workspace permissions and any provider charges
before passing `-AllowOpenCode` or `-AllowComfyUI`. The worker never starts
from the launcher dry run.

## Runtime integration (not automated yet)

1. OpenCode: install a supported Node.js release with npm, then run the opt-in
   installer in Ubuntu/WSL:

   ```bash
   bash scripts/install-opencode.sh --dry-run
   bash scripts/install-opencode.sh
   ```

   The script uses `npm install --global opencode-ai`, does not change
   existing OpenCode installations, and does not configure model providers.
   Review the upstream package before installing.
2. Ollama on Windows can be installed explicitly with `./install.ps1 -InstallOllama` (uses `winget install --id Ollama.Ollama --exact --source winget`). Use `-DryRun` to inspect first. The installer does not download a model or start the service. After starting Ollama, run `./scripts/ollama.ps1` to check local connectivity and list installed models. To explicitly download a model, run `./scripts/ollama.ps1 -DryRun -Pull -Model qwen2.5-coder:7b` first, then remove `-DryRun`. Model downloads can use multiple GB of storage. Select one Ollama host to avoid port conflicts.
3. ComfyUI source checkout on Windows: `./install.ps1 -DryRun -InstallComfyUISource`, then `./install.ps1 -InstallComfyUISource`. This clones the official repository into `~/ComfyUI` without overwriting an existing directory. It does **not** install Python, PyTorch, CUDA, dependencies or models. After installing Python 3.11, run `./install.ps1 -SetupComfyUIEnv` to create `~/ComfyUI/.venv`. To opt into downloading the upstream Python dependencies, use `./install.ps1 -InstallComfyUIRequirements`. This can download large packages and does not ensure a compatible GPU/PyTorch backend. After configuring the Python environment, run `./scripts/comfyui-gpu.ps1` for read-only NVIDIA driver and PyTorch CUDA diagnostics. Launch the existing environment with `./scripts/start-comfyui.ps1` (localhost only, default port 8188), then check its HTTP endpoint with `./scripts/comfyui-status.ps1`. Use `-DryRun` on either script to preview without running. CUDA PyTorch installation is available as a separate explicit opt-in: `./scripts/install-comfyui-torch.ps1 -DryRun -CudaWheel cu128` followed by the same command without `-DryRun`. Supported wheel choices are `cu126` and `cu128`; check driver compatibility before installing. The script verifies `torch.cuda.is_available()` after installation. Checkpoint downloads are supported as an explicit operation with a user-provided trusted HTTPS URL, filename and published SHA-256 checksum: `./scripts/download-comfyui-model.ps1 -DryRun -Url <https-url> -Sha256 <64-hex-digits> -FileName model.safetensors`; remove `-DryRun` after checking details. No default model is selected and no license is implicitly accepted. For an opt-in generation smoke test, export an API-format workflow from ComfyUI, then run `python -m scripts.comfyui_smoke workflow.json` to preview or `python -m scripts.comfyui_smoke workflow.json --submit` to submit and poll for output image metadata. The smoke test downloads returned PNG/JPEG/WebP image bytes into `comfyui-smoke-output/`, checks file signatures and size limits, records SHA-256 digests, and writes a JSON report. Existing output files are not overwritten. It has not yet been run against a real GPU in this project.
4. Test model API connectivity before enabling any agent workflow.
5. Connect Codex CLI and Claude Code only through supported, documented endpoints and keep their existing credentials untouched.

Do not expose Ollama, ComfyUI or any model gateway to the public internet. Do not commit credentials, model tokens or private workspaces. Paid-model calls and Git pushes require explicit approval.

## Current scope

The Windows installer performs preflight, optional Linux prerequisites, and opt-in Node.js/OpenCode setup. Node.js from Ubuntu apt may be too old. Ollama has an opt-in Windows winget installer, but ComfyUI has an opt-in source checkout, but its Python virtual environment and requirements can be installed with explicit switches; GPU-compatible PyTorch installation is opt-in with a runtime check; checkpoint download is opt-in and requires a trusted URL and checksum; end-to-end generation tests remain unverified. It does **not** install or run the full multi-agent platform yet.


### Save ComfyUI job results through the worker

For an image job payload, set `{"workflow": "/absolute/path/workflow-api.json", "save_outputs": true, "output_dir": "/absolute/path/outputs"}`. After explicit queue approval and starting the worker with `--allow-comfyui`, the worker submits the API workflow, waits for completion, downloads output images, writes a JSON report, and moves the job to `review`. The default image-job behavior without `save_outputs` remains status-only. Do not reuse output paths containing existing report filenames. Actual GPU integration is not yet verified.


### Dashboard image results

The local dashboard at `http://127.0.0.1:8787/` now accepts an optional absolute **Image output directory** when adding an image task. After authorizing execution and running the opt-in ComfyUI worker, image jobs that save outputs show their generated file count and whether a local report was saved. Final approval still requires the review button. The dashboard deliberately does not serve generated files over HTTP. CLI equivalent: `python -m workshop.cli add image "Generate art" --workflow /absolute/workflow.json --output-dir /absolute/output`. Actual ComfyUI GPU end-to-end execution remains unverified.


### Local image gallery

The dashboard shows up to 24 image previews for image tasks in review or done state. The layout adapts to narrow mobile screens. Each image URL is scoped to the job and signed; file bytes are checked against the recorded SHA-256 before delivery. The gallery is available only on the local dashboard. Run `python -m unittest discover -s tests -v` to exercise HTTP gallery tests.


### CI diagnostics

The validation workflow uploads unittest and runtime preflight logs for Windows and Ubuntu, Python 3.11 and 3.12. Open the GitHub Actions run, select the relevant job, and download the diagnostics artifact. Logs are retained for 14 days. The workflow preserves failing test exit codes, so a captured log does not conceal test failures.

## 서비스 연결 · 토큰 입력 · 프롬프트 테스트 (한 화면)
`.\start.ps1` (또는 WSL에서 `bash scripts/start.sh`)을 실행하면 브라우저가 `http://127.0.0.1:8787/setup`을 엽니다(`-NoBrowser`로 끌 수 있음).
1. 서비스를 고르고(Ollama / OpenCode Zen / OpenRouter / Groq / Cerebras / Gemini / Mistral / GitHub Models / 직접 입력),
2. 토큰을 붙여넣고, 모델 이름과 프롬프트를 쓴 뒤 **테스트 실행**을 누르면 짧은 텍스트 요청 1회(최대 300토큰)를 보내고 응답을 보여줍니다.
3. 성공하고 "저장"이 체크돼 있으면 서비스가 등록되어 보드 명세에서 `서비스이름/모델`로 쓸 수 있습니다. 목록에서 모델 목록 조회와 삭제(토큰 포함)도 됩니다.

안전 규칙: 토큰은 `~/.agent-workshop/secrets.json`(저장소 밖, WSL/Linux에서는 권한 600)에만 저장하고 화면·URL·로그·`providers.json`에 다시 나타나지 않습니다(마지막 4자리 마스킹만 표시). 테스트가 **성공했을 때만** 저장합니다.
클라우드 서비스는 동의 체크가 필요합니다. 이 화면은 AI 에이전트나 명령을 실행하지 않고 텍스트 요청만 보냅니다. OpenCode 로그인은 터미널의 `opencode auth login`을 쓰세요.
서비스 주소와 무료 한도는 확인되지 않은 기억 기준입니다. 실패하면 화면의 HTTP 번호(401 토큰, 404 모델/주소, 429 한도)를 보세요.

참고: 권한 600은 WSL/Linux 파일 시스템에서만 강제됩니다. Windows 네이티브 NTFS는 이 권한 비트가 없어 사용자 프로필 폴더의 기본 접근 권한에 의존합니다(Hub는 WSL에서 실행하는 것을 전제로 합니다).

## 프로그램처럼 쓰기: 앱 창 + 내장 터미널
```powershell
.\scripts\create-shortcut.ps1 -DryRun     # 먼저 무엇을 만들지 확인
.\scripts\create-shortcut.ps1             # 바탕화면에 "Agent Workshop" 바로가기 생성
```
바로가기를 실행하면 `start.ps1 -Terminal`이 WSL에서 허브를 시작하고, 서버가 뜨면 **Edge 앱 창**(주소창·탭 없는 별도 창)을 엽니다. Edge가 없으면 기본 브라우저가 열립니다. 직접 실행: `.\start.ps1 -Terminal`
(창을 열지 않으려면 `-NoBrowser`). 창 안에는 탭 4개가 있습니다: **설정 · 작업 · 보드 · 터미널**.

- **터미널 탭**은 WSL의 bash를 그대로 보여줍니다(색·방향키·대화형 화면 지원, 예: `opencode auth login`, `python3 -m workshop.onboard models`). 작업 폴더는 허브 저장소이고, Windows PATH를 섞지 않아 WSL 안에 설치한 OpenCode가 잡힙니다.
- 콘솔 창은 최소화되어 있지만 **서버를 담고 있으니 닫으면 허브가 종료**됩니다.

### 터미널 보안 (중요)
내장 터미널은 **사용자 계정 권한으로 명령을 실행하는 셸**입니다. 그래서 기본은 꺼져 있고 `-Terminal`(또는 `--enable-terminal`)을 줘야만 켜집니다.
- 입장에는 서버 시작 때마다 새로 만드는 **비공개 키 링크**(`/app?key=…`)가 필요합니다. 키는 `~/.agent-workshop/terminal.key`(권한 600)에 저장되고, 서버 로그에는 `key=***`로 가려집니다. 링크를 공유하지 마세요.
- 입장하면 HttpOnly·SameSite=Strict 쿠키가 발급되고 주소창에서 키가 사라집니다. 쿠키가 없는 접속은 토큰조차 받지 못합니다.
- WebSocket 연결은 loopback Host, 이 서버의 Origin, 쿠키, 첫 메시지의 토큰을 모두 통과해야 하고, 동시 세션은 3개로 제한됩니다. 연결이 끊기면 셸 프로세스도 종료됩니다.
- 허브는 계속 `127.0.0.1`에만 바인딩됩니다. 포트를 외부에 노출하지 마세요(포트포워딩·터널 금지).
- 같은 PC의 다른 프로그램이 이 셸에 접근하려면 키를 알아야 합니다. 키 파일을 읽을 수 있는 같은 사용자 계정의 프로세스는 어차피 같은 권한이라는 점은 변하지 않습니다.
