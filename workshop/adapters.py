"""Opt-in local integrations. No implicit CLI execution or paid API calls."""
import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

def capabilities():
    """Report executables without starting them."""
    return {name: shutil.which(name) is not None for name in ("opencode", "codex", "claude", "ollama", "git")}

def ollama_models(base_url="http://127.0.0.1:11434", timeout=3):
    """Read-only Ollama health check. Only loopback URLs accepted."""
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.username or parsed.password:
        raise ValueError("Ollama health check must target local HTTP loopback")
    with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=timeout) as response:
        data = json.load(response)
    return [item["name"] for item in data.get("models", [])]

MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/@-]*")

def _nvm_opencode():
    """Newest ~/.nvm/versions/node/*/bin/opencode, for shells that never sourced nvm.sh."""
    base = Path(os.environ.get("NVM_DIR") or Path.home() / ".nvm") / "versions" / "node"
    def version_key(path):
        parts = path.parent.parent.name.lstrip("v").split(".")
        return tuple(int(p) if p.isdigit() else 0 for p in parts)
    found = [p for p in base.glob("*/bin/opencode") if p.is_file() and os.access(p, os.X_OK)]
    return str(max(found, key=version_key)) if found else None

def find_opencode():
    """Path of a Linux/native opencode, or None.

    Under WSL, the Windows PATH exposes Windows installs (/mnt/c/.../opencode.exe). Running those
    from a Linux cwd hangs or mangles arguments and uses Windows credentials, so they are refused
    unless WORKSHOP_ALLOW_WINDOWS_OPENCODE=1. A native install under nvm is found even when the
    current shell has not loaded nvm.
    """
    binary = shutil.which("opencode")
    if binary and not binary.startswith("/mnt/"):
        return binary
    native = _nvm_opencode()
    if native:
        return native
    if binary and os.environ.get("WORKSHOP_ALLOW_WINDOWS_OPENCODE") == "1":
        return binary
    return None

def opencode_env(binary):
    """Environment with the binary's own directory first so its `node` shebang resolves."""
    env = os.environ.copy()
    env["PATH"] = str(Path(binary).parent) + os.pathsep + env.get("PATH", "")
    return env

def run_opencode(prompt, workspace, *, enabled=False, timeout=600, model=None):
    """Execute a single explicitly enabled OpenCode run in an existing worktree.

    Callers must review prompt, workspace, permissions and provider configuration.
    OpenCode may write files and incur provider charges.
    """
    if not enabled:
        raise PermissionError("OpenCode execution requires explicit opt-in")
    binary = find_opencode()
    if binary is None:
        found = shutil.which("opencode")
        if found and found.startswith("/mnt/"):
            raise FileNotFoundError("Only a Windows OpenCode was found (" + found + "). Install OpenCode "
                                    "inside WSL: bash scripts/install-opencode.sh")
        raise FileNotFoundError("OpenCode executable is not installed")
    directory = Path(workspace).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))
    if not (directory / ".git").exists():
        raise ValueError("Use a dedicated Git worktree or repository directory")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise ValueError("Prompt must be nonempty and <= 20000 characters")
    command = [binary, "run"]
    if model is not None:
        # provider/model as listed by `opencode models`; never passed through a shell.
        if not isinstance(model, str) or len(model) > 200 or not MODEL_PATTERN.fullmatch(model):
            raise ValueError("Model must look like provider/model")
        command += ["--model", model]
    completed = subprocess.run(
        command + [prompt], cwd=directory, capture_output=True, text=True,
        stdin=subprocess.DEVNULL,  # never wait for interactive input from a parent terminal
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
