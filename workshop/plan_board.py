"""Turn a natural-language request into a board spec (JSON) without running any step.

  python -m workshop.plan_board "REQUEST" --ollama-model qwen2.5-coder:7b --step-model opencode/glm-5.3-flash-free
  python -m workshop.plan_board "REQUEST" --opencode-model opencode/glm-5.3-flash-free --allow-opencode --allow-cloud --allow-may-train --step-model ...
  python -m workshop.plan_board "REQUEST" --plan plan.json --step-model ...   # plan from plan_cli
The spec is printed (or written with --out); review it, then run it with workshop.orchestrator.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from .adapters import run_opencode
from .modelpolicy import classify
from .orchestrator import strip_banner
from .planner import SYSTEM, plan_local, validate_plan

PLANNER_TIMEOUT = 240


def plan_to_spec(plan, *, request, step_model, code_model=None, repo=None, constraints=""):
    """Map planner tasks (code|qa|plan, zero-based depends_on) to board steps t1..tn."""
    validate_plan(plan)
    steps = []
    for index, task in enumerate(plan["tasks"]):
        step = {"id": "t" + str(index + 1), "model": step_model, "prompt": task["title"],
                "depends_on": ["t" + str(dep + 1) for dep in task["depends_on"]]}
        if task["kind"] == "code":
            if not repo:
                raise ValueError("Task " + str(index + 1) + " is a code task; pass --repo so it runs in a "
                                 "disposable worktree")
            step.update(kind="code", model=code_model or step_model, repo=repo)
        steps.append(step)
    title = (request.strip().splitlines() or ["plan"])[0][:80] or "plan"
    return {"title": title, "goal": request.strip(), "constraints": constraints, "steps": steps}


def extract_json(text):
    """First {...} object in a model reply, tolerating code fences and chatter."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("The planner reply contains no JSON object")
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        raise ValueError("The planner reply is not valid JSON") from None


def plan_with_opencode(request, model, run=run_opencode):
    prompt = (SYSTEM + "\n\nAnswer in plain text only: return the JSON object and nothing else. Do not create "
              "files or run commands.\n\nRequest:\n" + request)
    with tempfile.TemporaryDirectory(prefix="workshop-plan-") as scratch:
        subprocess.run(["git", "init", "-q", scratch], check=True, capture_output=True)
        result = run(prompt, scratch, enabled=True, model=model, timeout=PLANNER_TIMEOUT)
    if result.get("exit_code", 0) != 0:
        raise RuntimeError("OpenCode planner exited with code " + str(result["exit_code"]))
    return validate_plan(extract_json(strip_banner(result.get("stdout", ""))))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a board spec from a request (runs no step)")
    parser.add_argument("request")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", help="plan JSON file (output of workshop.plan_cli)")
    source.add_argument("--ollama-model", help="local Ollama model to plan with (loopback only)")
    source.add_argument("--opencode-model", help="OpenCode model to plan with (free/local unless --allow-paid-models)")
    parser.add_argument("--step-model", required=True, help="model for every step, e.g. opencode/glm-5.3-flash-free")
    parser.add_argument("--code-model", help="model for code tasks (default: --step-model)")
    parser.add_argument("--repo", help="git repository for code tasks (disposable worktree)")
    parser.add_argument("--constraints", default="")
    parser.add_argument("--out", help="write the spec here instead of stdout")
    parser.add_argument("--allow-opencode", action="store_true")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--allow-may-train", action="store_true")
    parser.add_argument("--allow-paid-models", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.plan:
            plan = validate_plan(json.loads(Path(args.plan).read_text(encoding="utf-8")))
        elif args.ollama_model:
            plan = plan_local(args.request, model=args.ollama_model)
        else:
            if not args.allow_opencode:
                raise PermissionError("OpenCode planning needs --allow-opencode")
            kind = classify(args.opencode_model)
            if kind == "paid" and not args.allow_paid_models:
                raise PermissionError("Planner model is paid or subscription-backed; pass --allow-paid-models")
            if kind != "local" and not (args.allow_cloud and args.allow_may_train):
                raise PermissionError("The request text is sent to a cloud service that may train on it; "
                                      "pass --allow-cloud --allow-may-train")
            plan = plan_with_opencode(args.request, args.opencode_model)
        repo = str(Path(args.repo).expanduser().resolve()) if args.repo else None
        spec = plan_to_spec(plan, request=args.request, step_model=args.step_model,
                            code_model=args.code_model, repo=repo, constraints=args.constraints)
    except (ValueError, PermissionError, RuntimeError, OSError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    text = json.dumps(spec, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("Spec written: " + args.out + "\nReview it, then: python3 -m workshop.orchestrator " + args.out
              + " --dry-run", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
