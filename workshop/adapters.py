"""Opt-in local integrations. No implicit CLI execution or paid API calls."""
import json
import os
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

def run_opencode(prompt, workspace, *, enabled=False, timeout=600):
    """Execute a single explicitly enabled OpenCode run in an existing worktree.

    Callers must review prompt, workspace, permissions and provider configuration.
    OpenCode may write files and incur provider charges.
    """
    if not enabled:
        raise PermissionError("OpenCode execution requires explicit opt-in")
    binary = shutil.which("opencode")
    if binary is None:
        raise FileNotFoundError("OpenCode executable is not installed")
    directory = Path(workspace).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise NotADirectoryError(str(directory))
    if not (directory / ".git").exists():
        raise ValueError("Use a dedicated Git worktree or repository directory")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20000:
        raise ValueError("Prompt must be nonempty and <= 20000 characters")
    completed = subprocess.run(
        [binary, "run", prompt], cwd=directory, capture_output=True, text=True,
        timeout=timeout, check=False, env=os.environ.copy(),
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
