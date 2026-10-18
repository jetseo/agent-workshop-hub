"""Non-destructive local integration diagnostics.

Run: python -m workshop.diagnostics
Optional: python -m workshop.diagnostics --json --strict
No model inference, external network calls, or persistent job mutations.
"""
import argparse
import json
import sqlite3
import tempfile
from pathlib import Path

from .adapters import comfyui_health, ollama_models
from .preflight import inspect_environment
from .queue import Queue


def _check_service(fn, label):
    try:
        value = fn()
        return {"ok": True, "detail": label(value)}
    except (OSError, ValueError, KeyError, TypeError, TimeoutError) as error:
        return {"ok": False, "detail": type(error).__name__ + ": " + str(error)[:250]}


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


def inspect_runtime(*, check_services=True):
    checks = inspect_environment()
    checks["queue_self_test"] = _queue_self_test()
    if check_services:
        checks["ollama_api"] = _check_service(
            ollama_models, lambda models: str(len(models)) + " local models available")
        checks["comfyui_api"] = _check_service(
            comfyui_health, lambda stats: "Local ComfyUI API reachable")
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description="Non-destructive AI workshop diagnostics")
    parser.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    parser.add_argument("--strict", action="store_true",
                        help="Require enabled optional integrations to be reachable")
    parser.add_argument("--no-services", action="store_true",
                        help="Skip loopback Ollama and ComfyUI connectivity checks")
    args = parser.parse_args(argv)
    checks = inspect_runtime(check_services=not args.no_services)
    if args.json:
        print(json.dumps(checks, ensure_ascii=False, indent=2))
    else:
        for name, item in checks.items():
            print(("[OK] " if item["ok"] else "[--] ") +
                  name + ": " + str(item["detail"]))
        print("Optional services are not required unless --strict is used.")
    mandatory = ("python", "platform", "git", "queue_self_test")
    required = tuple(checks) if args.strict else mandatory
    return 0 if all(checks[key]["ok"] for key in required) else 1


if __name__ == "__main__":
    raise SystemExit(main())
