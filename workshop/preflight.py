"""Local runtime self-check: python -m workshop.preflight."""
import json
import os
import platform
import shutil
import socket
import sys
from pathlib import Path

def describe_error(error):
    """Short Korean explanation of a failed local-service call, keeping the raw cause for debugging."""
    cause = getattr(error, "reason", error)  # urllib wraps the OSError in URLError.reason
    raw = (type(error).__name__ + ": " + str(cause))[:250]
    if isinstance(cause, (TimeoutError, socket.timeout)):
        return "응답 시간 초과 - 서비스가 멈췄거나 매우 느립니다 (" + raw + ")"
    if isinstance(cause, ConnectionRefusedError) or getattr(cause, "errno", None) in (111, 10061):
        return "연결 거부 - 서비스가 실행 중이 아닙니다 (" + raw + ")"
    if isinstance(cause, OSError):
        return "연결 실패 - 주소/포트와 방화벽을 확인하세요 (" + raw + ")"
    if isinstance(error, ValueError):
        return "응답 형식이 올바르지 않습니다 (" + raw + ")"
    return raw


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
