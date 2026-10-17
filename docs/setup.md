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
