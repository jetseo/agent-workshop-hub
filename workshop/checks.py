"""Build/test checks run by JetAI itself in the agent's copy of the project.

Only commands JetAI detected from the project files (`projectmap`) and only the build and test kinds are run: the
caller cannot pass an arbitrary command. They run in the throw-away copy, with a minimal environment, a time limit and
no shell. This executes the project's own build/test code (including what the agent wrote), so it starts only when the
user asks. The result is a plain fact list: passed / failed / timeout / unavailable.
"""
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import codereport, projectmap

KINDS = ("build", "test")
OUTPUT_TAIL = 1500
MAX_OUTPUT = 400_000


def plan(directory):
    """Detected build/test commands for a folder: [{kind, command, argv}] (argv is None when the tool is not installed)."""
    root = Path(directory)
    detected = projectmap.detect_commands(root, projectmap.list_files(root))
    items = []
    for entry in detected:
        if entry["kind"] not in KINDS:
            continue
        argv = shlex.split(entry["command"], posix=True)
        if argv[0] in ("python", "python3"):
            argv[0] = sys.executable
        else:
            found = shutil.which(argv[0])
            argv[0] = found
        items.append({"kind": entry["kind"], "command": entry["command"], "source": entry["source"], "argv": argv if argv[0] else None})
    return items


def run_one(item, directory, env, timeout, popen, kill, clock=time.time):
    began = clock()
    result = {"kind": item["kind"], "command": item["command"], "status": "unavailable", "exit": None, "seconds": 0.0, "output": ""}
    if not item["argv"]:
        result["output"] = "이 PC에 실행 도구가 없습니다."
        return result
    try:
        proc = popen(item["argv"], cwd=str(directory), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError as error:
        result["output"] = "시작하지 못했습니다: " + str(error)[:160]
        return result
    try:
        out, _ = proc.communicate(timeout=timeout)
        result["status"] = "passed" if proc.returncode == 0 else "failed"
        no_tests = proc.returncode == 5 and b"no tests ran" in (out or b"").lower()
        if no_tests:
            result["status"] = "notests"                                  # the runner ran but found nothing to run: not a pass
    except subprocess.TimeoutExpired:
        kill(proc)
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            out = b""
        result["status"] = "timeout"
    result["exit"] = proc.returncode
    text = (out or b"")[:MAX_OUTPUT].decode("utf-8", "replace").strip()
    result["output"] = codereport.mask(text[-OUTPUT_TAIL:])
    result["seconds"] = round(clock() - began, 1)
    return result


def verdict(results):
    if not results:
        return "none"
    if any(r["status"] in ("failed", "timeout") for r in results):
        return "failed"
    if all(r["status"] in ("unavailable", "notests") for r in results):
        return "notests" if any(r["status"] == "notests" for r in results) else "unavailable"
    return "passed"
