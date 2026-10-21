"""Read-only look at this PC for the first-run screen: nothing is installed, started or downloaded."""
import platform
import shutil
import sys


def inspect(ollama_fn=None, which=shutil.which):
    if ollama_fn is None:
        from .adapters import ollama_models as ollama_fn
    try:
        models = list(ollama_fn(timeout=1))
        ollama = {"running": True, "models": models[:20]}
    except Exception:                    # not installed, not running or not answering: all just "not available"
        ollama = {"running": False, "models": []}
    system = platform.system()
    return {"os": system, "python": "%d.%d.%d" % sys.version_info[:3], "ollama": ollama,
            "wsl": {"applicable": system == "Windows", "available": bool(which("wsl.exe") or which("wsl"))},
            "opencode": bool(which("opencode"))}
