"""Non-destructive local integration diagnostics.

Run: python -m workshop.diagnostics
Optional: python -m workshop.diagnostics --json --strict
No model inference, external network calls, or persistent job mutations.
"""
import argparse
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from .adapters import comfyui_health, ollama_models
from .preflight import describe_error, inspect_environment
from .queue import Queue


def _check_service(fn, label):
    try:
        value = fn()
        return {"ok": True, "detail": label(value)}
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as error:
        return {"ok": False, "detail": describe_error(error)}


def _queue_self_test():
    try:
        with tempfile.TemporaryDirectory(prefix="workshop-diagnostics-") as folder:
            queue = Queue(Path(folder) / "jobs.sqlite3")
            job_id = queue.add("code", "Diagnostic only", {"workspace": folder},
                               require_approval=True)
            if queue.claim(kinds=["code"]) is not None:
                raise AssertionError("Unapproved job was claimed")
            queue.authorize(job_id)
            claimed = queue.claim(kinds=["code"])
            if claimed is None or claimed["id"] != job_id:
                raise AssertionError("Approved job was not claimed")
            if queue.finish(job_id, {"diagnostic": True}) != "review":
                raise AssertionError("Job did not reach review")
            queue.approve(job_id)
            if queue.list()[0]["state"] != "done":
                raise AssertionError("Job did not reach done")
        return {"ok": True, "detail": "SQLite approval, claim, review and completion passed"}
    except (OSError, sqlite3.Error, AssertionError, ValueError) as error:
        return {"ok": False, "detail": type(error).__name__ + ": " + str(error)[:250]}


# Actionable next steps for failed checks (Korean, with the command to run).
HINTS = {
    "python": "Python 3.9 이상이 필요합니다. WSL: sudo apt install python3 / Windows: python.org 설치 후 새 터미널에서 다시 실행하세요.",
    "platform": "Linux(WSL2)와 Windows만 지원합니다. macOS는 아직 검증되지 않았습니다.",
    "git": "git이 필요합니다. Ubuntu(WSL): sudo apt install git",
    "opencode": "WSL 안에 설치되지 않았거나 Windows용만 보입니다. bash scripts/install-opencode.sh --dry-run 으로 절차를 확인한 뒤 설치하세요.",
    "ollama": "선택 사항: Windows에서 .\\scripts\\ollama.ps1 -DryRun 으로 확인하세요.",
    "ollama_api": "Ollama가 꺼져 있습니다. 'ollama serve' 실행 후 다시 확인하세요 (127.0.0.1:11434).",
    "comfyui_api": "ComfyUI가 꺼져 있습니다. scripts/start-comfyui.ps1 로 시작 후 다시 확인하세요 (127.0.0.1:8188).",
    "queue_self_test": "임시 SQLite 자체 테스트 실패: 디스크 공간과 임시 폴더 쓰기 권한을 확인하세요.",
}


def _add_hints(checks):
    for name, item in checks.items():
        if not item["ok"] and name in HINTS:
            item["hint"] = HINTS[name]
    return checks


def inspect_runtime(*, check_services=True):
    checks = inspect_environment()
    checks["queue_self_test"] = _queue_self_test()
    if check_services:
        checks["ollama_api"] = _check_service(
            ollama_models, lambda models: str(len(models)) + " local models available")
        checks["comfyui_api"] = _check_service(
            comfyui_health, lambda stats: "Local ComfyUI API reachable")
    return _add_hints(checks)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Non-destructive AI workshop diagnostics")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--strict", action="store_true",
                        help="Require enabled optional integrations to be reachable")
    parser.add_argument("--no-services", action="store_true",
                        help="Skip loopback Ollama and ComfyUI connectivity checks")
    args = parser.parse_args(argv)
    # Korean hints must not crash legacy Windows code pages (cp1252/cp949 pipes).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    checks = inspect_runtime(check_services=not args.no_services)
    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        for name, item in checks.items():
            print(("[OK] " if item["ok"] else "[--] ") +
                  name + ": " + str(item["detail"]))
            if item.get("hint"):
                print("     -> " + item["hint"])
        print("Optional services are not required unless --strict is used.")
    mandatory = ("python", "platform", "git", "queue_self_test")
    required = tuple(checks) if args.strict else mandatory
    return 0 if all(checks[key]["ok"] for key in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
