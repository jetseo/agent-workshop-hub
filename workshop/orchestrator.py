"""Run a board's steps serially or in parallel. Opt-in; never retries code steps.

Spec file (JSON): {"title","goal","constraints","steps":[{"id","kind":"text|code",
"model":"provider/model","depends_on":[...],"prompt","workspace"}]}
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adapters import run_opencode
from .board import Board
from .chat import chat
from .modelpolicy import classify
from .providers import Providers

OPENCODE_PROVIDERS = ("opencode", "openai")  # served by the OpenCode CLI, not a chat endpoint
NO_TOOLS_NOTE = ("Answer in plain text only. Do not create, modify or delete files and do not run "
                 "commands.\n\n")
TEXT_ATTEMPTS = 2  # text steps have no side effects, so one retry is safe


def create_board(root, spec):
    board = Board.create(root, spec["title"], spec["goal"], spec.get("constraints", ""))
    for step in spec["steps"]:
        board.add_step(step["id"], step["model"], step.get("depends_on", ()),
                       step.get("prompt", ""), step.get("kind", "text"), step.get("workspace"))
    return board


def strip_banner(text):
    """Drop OpenCode's leading '> agent · model' header line(s)."""
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("> ")):
        lines.pop(0)
    return "\n".join(lines).strip()


def _opencode_text(prompt, step, flags, sid, code_fn):
    """Text step served by OpenCode in a throw-away git dir. Still a tool-capable agent,
    so it needs the same explicit enable + approval as code steps, and free/local models."""
    if not flags["allow_opencode"]:
        raise PermissionError("OpenCode execution not enabled (--allow-opencode)")
    if sid not in flags["approved_code"] and not flags["approve_text_agents"]:
        raise PermissionError("Step needs approval (--approve-code " + sid + " or --approve-text-agents)")
    if classify(step["model"]) == "paid" and not flags["allow_paid"]:
        raise PermissionError("Model '" + step["model"] + "' is paid or subscription-backed; "
                              "use a -free/ollama model or pass --allow-paid-models")
    with tempfile.TemporaryDirectory(prefix="workshop-text-") as scratch:
        subprocess.run(["git", "init", "-q", scratch], check=True, capture_output=True)
        result = code_fn(NO_TOOLS_NOTE + prompt, scratch, enabled=True, model=step["model"])
    if result.get("exit_code", 0) != 0:
        raise RuntimeError("OpenCode exited with code " + str(result["exit_code"]))
    return strip_banner(result.get("stdout", ""))


def _run_step(board, sid, step, providers, flags, text_fn, code_fn):
    prompt = board.context_for(sid)
    provider_name, _, model = step["model"].partition("/")
    if step["kind"] == "code":
        if sid not in flags["approved_code"]:
            raise PermissionError("Code step needs explicit approval (--approve-code " + sid + ")")
        if not flags["allow_opencode"]:
            raise PermissionError("OpenCode execution not enabled (--allow-opencode)")
        if not step.get("workspace"):
            raise ValueError("Code step requires a workspace")
        if classify(step["model"]) == "paid" and not flags["allow_paid"]:
            raise PermissionError("Model '" + step["model"] + "' is paid or subscription-backed; "
                                  "use a -free/ollama model or pass --allow-paid-models")
        result = code_fn(prompt, step["workspace"], enabled=True, model=step["model"])
        if result.get("exit_code", 0) != 0:
            raise RuntimeError("OpenCode exited with code " + str(result["exit_code"]))
        return strip_banner(result.get("stdout", ""))
    provider = providers.get(provider_name)
    if provider is None and provider_name in OPENCODE_PROVIDERS:
        return _opencode_text(prompt, step, flags, sid, code_fn)
    if provider is None:
        raise ValueError("Provider '" + provider_name + "' is not registered (python -m workshop.onboard add)")
    policy = provider["data_policy"]
    if policy != "local" and not flags["allow_cloud"]:
        raise PermissionError("Cloud provider requires --allow-cloud (data leaves this PC)")
    if policy in ("cloud-may-train", "unknown") and not flags["allow_may_train"]:
        raise PermissionError("Provider may train on inputs; requires --allow-may-train")
    last = None
    for _ in range(TEXT_ATTEMPTS):
        try:
            return text_fn(provider, model, [{"role": "user", "content": prompt}])
        except PermissionError:
            raise
        except Exception as error:
            last = error
    raise last


def run_board(board, providers, *, allow_opencode=False, allow_cloud=False, allow_may_train=False,
              allow_paid=False, approve_text_agents=False, approved_code=(), max_parallel=2, text_fn=chat, code_fn=run_opencode, progress=None):
    flags = {"allow_opencode": allow_opencode, "allow_cloud": allow_cloud,
             "allow_may_train": allow_may_train, "allow_paid": allow_paid, "approve_text_agents": approve_text_agents,
             "approved_code": set(approved_code)}
    errors = {}
    say = progress or (lambda message: None)
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        while True:
            ready = board.ready_steps()
            if not ready:
                break
            steps = board.steps()
            for sid in ready:
                board.set_status(sid, "running")
                say("[start] " + sid + "  (" + steps[sid]["model"] + ")")
            started = time.time()
            futures = {sid: pool.submit(_run_step, board, sid, steps[sid], providers, flags, text_fn, code_fn)
                       for sid in ready}
            for sid, future in futures.items():
                try:
                    board.write_step_output(sid, future.result())
                    board.merge_step(sid)  # single writer: only this loop merges
                    say("[done]  " + sid + "  " + str(int(time.time() - started)) + "s")
                except Exception as error:
                    errors[sid] = type(error).__name__ + ": " + str(error)[:300]
                    board.set_status(sid, "failed")
                    say("[FAIL]  " + sid + "  " + errors[sid])
    steps = board.steps()
    return {"done": [s for s, v in steps.items() if v["status"] == "done"],
            "failed": errors,
            "blocked": [s for s, v in steps.items() if v["status"] == "pending"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a multi-model board")
    parser.add_argument("spec", help="JSON spec file")
    parser.add_argument("--root", default=str(Path.home() / ".agent-workshop" / "boards"))
    parser.add_argument("--allow-opencode", action="store_true")
    parser.add_argument("--approve-code", action="append", default=[], metavar="STEP_ID")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--allow-may-train", action="store_true")
    parser.add_argument("--approve-text-agents", action="store_true",
                        help="Approve all text-only steps served by OpenCode (never code steps)")
    parser.add_argument("--allow-paid-models", action="store_true",
                        help="Permit paid/subscription-backed OpenCode models (default: free and local only)")
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true", help="Create the board only; call no model")
    args = parser.parse_args(argv)
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    board = create_board(args.root, spec)
    print("Board: " + str(board.folder / "board.md"), flush=True)
    if args.dry_run:
        return 0
    summary = run_board(board, Providers(), allow_opencode=args.allow_opencode,
                        allow_cloud=args.allow_cloud, allow_may_train=args.allow_may_train,
                        allow_paid=args.allow_paid_models,
                        approve_text_agents=args.approve_text_agents, approved_code=args.approve_code, max_parallel=args.max_parallel,
                        progress=lambda m: print(m, file=sys.stderr, flush=True))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] and not summary["blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
