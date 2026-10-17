# Installation and safety

## Windows preflight

In PowerShell from the repository root:

```powershell
.\install.ps1 -DryRun
.\install.ps1
```

If WSL is missing, use an elevated PowerShell and explicitly opt in:

```powershell
.\install.ps1 -InstallWSL
```

Reboot if requested and complete the Ubuntu user setup. To install only base Linux prerequisites:

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

1. Install OpenCode from its official installation instructions.
2. Install Ollama using its official Windows or Linux instructions. Select one host to avoid port conflicts.
3. Install ComfyUI in Windows with NVIDIA GPU support and test image generation independently.
4. Test model API connectivity before enabling any agent workflow.
5. Connect Codex CLI and Claude Code only through supported, documented endpoints and keep their existing credentials untouched.

Do not expose Ollama, ComfyUI or any model gateway to the public internet. Do not commit credentials, model tokens or private workspaces. Paid-model calls and Git pushes require explicit approval.

## Current scope

The installer only performs preflight and optional Linux prerequisite installation. It does **not** install or run the full multi-agent platform yet.
