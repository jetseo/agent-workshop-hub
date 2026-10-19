"""Run a board's steps serially or in parallel. Opt-in; never retries code steps.

Spec file (JSON): {"title","goal","constraints","steps":[{"id","kind":"text|code",
"model":"provider/model","depends_on":[...],"prompt","workspace"}]}
"""
import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .adapters import run_opencode
from .board import Board
from .chat import chat
from .providers import Providers

TEXT_ATTEMPTS = 2  # text steps have no side effects, so one retry is safe


def create_board(root, spec):
    board = Board.create(root, spec["title"], spec["goal"], spec.get("constraints", ""))
    for step in spec["steps"]:
        board.add_step(step["id"], step["model"], step.get("depends_on", ()),
                       step.get("prompt", ""), step.get("kind", "text"), step.get("workspace"))
    return board


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
        result = code_fn(prompt, step["workspace"], enabled=True, model=step["model"])
        if result.get("exit_code", 0) != 0:
            raise RuntimeError("OpenCode exited with code " + str(result["exit_code"]))
        return result.get("stdout", "")
    provider = providers.get(provider_name)
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
              approved_code=(), max_parallel=2, text_fn=chat, code_fn=run_opencode):
    flags = {"allow_opencode": allow_opencode, "allow_cloud": allow_cloud,
             "allow_may_train": allow_may_train, "approved_code": set(approved_code)}
    errors = {}
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        while True:
            ready = board.ready_steps()
            if not ready:
                break
            steps = board.steps()
            for sid in ready:
                board.set_status(sid, "running")
            futures = {sid: pool.submit(_run_step, board, sid, steps[sid], providers, flags, text_fn, code_fn)
                       for sid in ready}
            for sid, future in futures.items():
                try:
                    board.write_step_output(sid, future.result())
                    board.merge_step(sid)  # single writer: only this loop merges
                except Exception as error:
                    errors[sid] = type(error).__name__ + ": " + str(error)[:300]
                    board.set_status(sid, "failed")
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
                        approved_code=args.approve_code, max_parallel=args.max_parallel)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] and not summary["blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
