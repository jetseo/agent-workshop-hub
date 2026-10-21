"""Local runtime self-check: python -m workshop.preflight."""
import json
import os
import platform
import shutil
import sys
from pathlib import Path

def inspect_environment():
    checks = {}
    checks["python"] = {
        "ok": sys.version_info >= (3, 9),
        "detail": platform.python_version(),
    }
    checks["platform"] = {
        "ok": sys.platform.startswith("linux") or sys.platform == "win32",
        "detail": platform.platform(),
    }
    for name in ("git", "opencode", "ollama"):
        checks[name] = {"ok": shutil.which(name) is not None,
                        "detail": shutil.which(name) or "not on PATH"}
    found = shutil.which("opencode")
    if found and found.startswith("/mnt/"):
        checks["opencode"] = {"ok": False, "detail": "Windows binary via WSL interop: " + found}
    checks["sqlite"] = {"ok": True, "detail": "Python standard library"}
    return checks

def main():
    checks = inspect_environment()
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    mandatory = ("python", "platform", "git")
    return 0 if all(checks[name]["ok"] for name in mandatory) else 1

if __name__ == "__main__":
    raise SystemExit(main())
