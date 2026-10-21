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
from .cache import ResultCache
from .chat import chat
from .modelpolicy import classify
from . import worktree
from .providers import Providers
from .router import ROLES, route
from .runstats import RunStats
from .usage import UsageLedger, estimate_tokens, tier_of

OPENCODE_PROVIDERS = ("opencode", "openai")  # served by the OpenCode CLI, not a chat endpoint
CODE_NOTE = ("Make only the requested change inside the current working directory. Do not commit, push, "
             "merge, install packages, or touch files outside this directory.\n\n")
NO_TOOLS_NOTE = ("Answer in plain text only. Do not create, modify or delete files and do not run "
                 "commands.\n\n")
TEXT_TIMEOUT = 240  # seconds per OpenCode text step
TEXT_ATTEMPTS = 2  # text steps have no side effects, so one retry is safe


SECRET_NAME_PARTS = (".env", "auth.json", "id_rsa", "id_ed25519", ".pem", ".key", ".pfx", ".p12",
                     "secret", "credential", "token", "password", ".sqlite", ".db")
MAX_REF_FILES, MAX_REF_BYTES, MAX_REF_TOTAL = 10, 40_000, 100_000


def load_references(names, context_root=None):
    """Read user-chosen files as read-only prompt context. Refuses anything outside the root,
    symlinks, secret-looking names, and oversized input."""
    root = Path(context_root or Path.cwd()).resolve()
    if not isinstance(names, list) or len(names) > MAX_REF_FILES:
        raise ValueError("context_files must be a list of at most " + str(MAX_REF_FILES) + " paths")
    refs, total = [], 0
    for name in names:
        if not isinstance(name, str) or not name or Path(name).is_absolute():
            raise ValueError("context_files entries must be relative paths: " + str(name))
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Not a regular file under the context root: " + name)
        resolved = path.resolve()
        if root not in resolved.parents:
            raise ValueError("Path escapes the context root: " + name)
        lowered = resolved.name.lower()
        if any(part in lowered for part in SECRET_NAME_PARTS):
            raise ValueError("Refusing secret-looking file: " + name)
        size = resolved.stat().st_size
        total += size
        if size > MAX_REF_BYTES or total > MAX_REF_TOTAL:
            raise ValueError("Reference files too large: " + name)
        refs.append({"name": name, "text": resolved.read_text(encoding="utf-8", errors="replace")})
    return refs


def _resolve_repo(repo):
    if repo is None:
        return None
    path = Path(repo).expanduser().resolve()
    if not worktree.is_repo(path):
        raise ValueError("Step repo is not a git repository: " + str(path))
    return str(path)


def create_board(root, spec, context_root=None):
    board = Board.create(root, spec["title"], spec["goal"], spec.get("constraints", ""),
                         references=load_references(spec.get("context_files", []), context_root))
    for step in spec["steps"]:
        board.add_step(step["id"], step["model"], step.get("depends_on", ()),
                       step.get("prompt", ""), step.get("kind", "text"), step.get("workspace"),
                       _resolve_repo(step.get("repo")), role=_valid_role(step.get("role")))
    return board


def _valid_role(role):
    if role is not None and role not in ROLES:
        raise ValueError("Unknown step role: " + str(role))
    return role


def _exit_error(result):
    detail = (result.get("stderr") or result.get("stdout") or "").strip()
    return "OpenCode exited with code " + str(result["exit_code"]) + (": " + detail[-300:] if detail else "")


def strip_banner(text):
    """Drop OpenCode's leading '> agent · model' header line(s)."""
    lines = text.splitlines()
    while lines and (not lines[0].strip() or lines[0].lstrip().startswith("> ")):
        lines.pop(0)
    return "\n".join(lines).strip()


_TIER = {"local": "local", "free": "free", "paid": "other"}


def _record(flags, step, tier, sent, received):
    """Book one successful step in the usage ledger. Step runs report no token counts, so these are estimates."""
    ledger = flags.get("ledger")
    if ledger is None:
        return
    name, _, model = step["model"].partition("/")
    try:
        ledger.record(provider=name, model=model, tier=tier, prompt_tokens=estimate_tokens(sent),
                      completion_tokens=estimate_tokens(received), estimated=True, source="board")
    except OSError:
        pass                                   # a full disk must never fail a step


def _require_cloud_consent(step, flags):
    if classify(step["model"]) != "local" and not (flags["allow_cloud"] and flags["allow_may_train"]):
        raise PermissionError("Model '" + step["model"] + "' runs on a cloud service that may train on your "
                              "prompts and files; pass --allow-cloud --allow-may-train to accept that")


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
    _require_cloud_consent(step, flags)
    with tempfile.TemporaryDirectory(prefix="workshop-text-") as scratch:
        subprocess.run(["git", "init", "-q", scratch], check=True, capture_output=True)
        result = code_fn(NO_TOOLS_NOTE + prompt, scratch, enabled=True, model=step["model"],
                         timeout=TEXT_TIMEOUT)
    if result.get("exit_code", 0) != 0:
        raise RuntimeError(_exit_error(result))
    _record(flags, step, _TIER[classify(step["model"])], NO_TOOLS_NOTE + prompt, result.get("stdout", ""))
    return strip_banner(result.get("stdout", ""))


def _run_step(board, sid, step, providers, flags, text_fn, code_fn):
    prompt = board.context_for(sid)
    provider_name, _, model = step["model"].partition("/")
    if step["kind"] == "code":
        if sid not in flags["approved_code"]:
            raise PermissionError("Code step needs explicit approval (--approve-code " + sid + ")")
        if not flags["allow_opencode"]:
            raise PermissionError("OpenCode execution not enabled (--allow-opencode)")
        if not (step.get("repo") or step.get("workspace")):
            raise ValueError("Code step requires a repo (disposable worktree) or a workspace")
        if classify(step["model"]) == "paid" and not flags["allow_paid"]:
            raise PermissionError("Model '" + step["model"] + "' is paid or subscription-backed; "
                                  "use a -free/ollama model or pass --allow-paid-models")
        _require_cloud_consent(step, flags)
        if step.get("repo"):
            path, branch = worktree.create(step["repo"], flags["worktree_base"], board.folder.name, sid)
            result = code_fn(CODE_NOTE + prompt, str(path), enabled=True, model=step["model"])
            if result.get("exit_code", 0) != 0:
                raise RuntimeError(_exit_error(result) + " (worktree kept for inspection: " + str(path) + ")")
            info = worktree.collect_diff(path)
            _record(flags, step, _TIER[classify(step["model"])], CODE_NOTE + prompt, result.get("stdout", ""))
            return ("Worktree: " + str(path) + "\nBranch: " + branch + "\n"
                    "Nothing was committed, pushed or merged. Review the diff, then merge or discard it yourself.\n\n"
                    + ("No files changed." if not info["changed"] else
                       info["stat"] + "\n\n```diff\n" + info["diff"] + ("\n[...diff truncated...]" if info["truncated"] else "") + "\n```"))
        if not (Path(step["workspace"]) / ".git").is_file() and not flags["allow_main_checkout"]:
            raise PermissionError("Workspace is a main checkout, not a disposable worktree; use a step "
                                  "\"repo\" instead, or pass --allow-main-checkout to accept direct edits")
        result = code_fn(CODE_NOTE + prompt, step["workspace"], enabled=True, model=step["model"])
        if result.get("exit_code", 0) != 0:
            raise RuntimeError(_exit_error(result))
        _record(flags, step, _TIER[classify(step["model"])], CODE_NOTE + prompt, result.get("stdout", ""))
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
    cache = flags.get("cache")           # consent gates above always run first, even for a cached reply
    if cache is not None:
        try:
            hit = cache.get(step["model"], prompt)
        except OSError:
            hit = None
        if hit is not None:
            return hit                    # no model call, nothing booked in the usage ledger
    last = None
    for _ in range(TEXT_ATTEMPTS):
        try:
            reply = text_fn(provider, model, [{"role": "user", "content": prompt}])
            _record(flags, step, tier_of(policy, model), prompt, reply)     # only the attempt that succeeded
            if cache is not None:
                try:
                    cache.put(step["model"], prompt, reply)
                except OSError:
                    pass
            return reply
        except PermissionError:
            raise
        except Exception as error:
            last = error
    raise last


def _timed_step(*args):
    started = time.time()
    return _run_step(*args), time.time() - started


def _note_outcome(stats, model, step, ok, seconds):
    if stats is None or model == "auto":
        return
    try:
        stats.record(model, ok=ok, seconds=seconds, kind=step.get("kind", "text"))
    except OSError:
        pass                                   # statistics must never fail a step


def _tier_fn(providers):
    """Tier of a model id: registered providers by their data policy (local/-free), else by name."""
    def tier(model):
        name, _, short = model.partition("/")
        entry = providers.get(name)
        if entry is not None:
            return {"local": "local", "free": "free"}.get(tier_of(entry["data_policy"], short), "paid")
        return classify(model)
    return tier


def _resolve_auto(board, ready, auto_models, stats, errors, say, providers):
    """Replace model "auto" with the router's pick (local/free only). Steps with no usable model fail
    here, before anything is sent anywhere."""
    steps = board.steps()
    runnable = []
    history = None
    for sid in ready:
        step = steps[sid]
        if step["model"] != "auto":
            runnable.append(sid)
            continue
        if history is None:
            try:
                history = stats.load() if stats is not None else {}
            except OSError:
                history = {}
        decision = route(board.context_for(sid), auto_models, kind=step.get("kind", "text"),
                         role=step.get("role") or "coder", stats=history,
                         tier_fn=_tier_fn(providers))
        if decision["model"] is None:
            errors[sid] = "RoutingError: " + decision["reason"] + " (pass --auto-model with local/free models)"
            board.set_status(sid, "failed")
            say("[FAIL]  " + sid + "  " + errors[sid])
            continue
        board.set_model(sid, decision["model"])
        say("[route] " + sid + " -> " + decision["model"] + "  (" + decision["reason"] + ")")
        runnable.append(sid)
    return runnable


def run_board(board, providers, *, allow_opencode=False, allow_cloud=False, allow_may_train=False,
              allow_paid=False, approve_text_agents=False, allow_main_checkout=False,
              worktree_base=None, approved_code=(), max_parallel=2, text_fn=chat, code_fn=run_opencode, progress=None, ledger=None,
              auto_models=(), stats=None, cache=None):
    flags = {"ledger": ledger, "cache": cache, "allow_opencode": allow_opencode, "allow_cloud": allow_cloud,
             "allow_may_train": allow_may_train, "allow_paid": allow_paid, "approve_text_agents": approve_text_agents,
             "allow_main_checkout": allow_main_checkout,
             "worktree_base": worktree_base or worktree.default_base(),
             "approved_code": set(approved_code)}
    errors = {}
    say = progress or (lambda message: None)
    with ThreadPoolExecutor(max_workers=max(1, max_parallel)) as pool:
        while True:
            ready = board.ready_steps()
            if not ready:
                break
            ready = _resolve_auto(board, ready, auto_models, stats, errors, say, providers)
            steps = board.steps()
            for sid in ready:
                board.set_status(sid, "running")
                say("[start] " + sid + "  (" + steps[sid]["model"] + ")")
            started = time.time()
            futures = {sid: pool.submit(_timed_step, board, sid, steps[sid], providers, flags, text_fn, code_fn)
                       for sid in ready}
            for sid, future in futures.items():
                model = steps[sid]["model"]
                try:
                    output, seconds = future.result()
                    board.write_step_output(sid, output)
                    board.merge_step(sid)  # single writer: only this loop merges
                    _note_outcome(stats, model, steps[sid], True, seconds)
                    say("[done]  " + sid + "  " + str(int(time.time() - started)) + "s")
                except Exception as error:
                    errors[sid] = type(error).__name__ + ": " + str(error)[:300]
                    board.set_status(sid, "failed")
                    # Refusals (missing approval/consent) say nothing about model quality.
                    if not isinstance(error, PermissionError):
                        _note_outcome(stats, model, steps[sid], False, time.time() - started)
                    say("[FAIL]  " + sid + "  " + errors[sid])
    steps = board.steps()
    return {"done": [s for s, v in steps.items() if v["status"] == "done"],
            "failed": errors,
            "blocked": [s for s, v in steps.items() if v["status"] == "pending"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a multi-model board")
    parser.add_argument("spec", nargs="?", help="JSON spec file (omit with --resume)")
    parser.add_argument("--resume", metavar="BOARD_DIR", help="Continue an existing board: skips finished steps, re-runs failed text steps")
    parser.add_argument("--retry-code", action="append", default=[], metavar="STEP_ID", help="Also re-run this failed code step (it may have changed files)")
    parser.add_argument("--root", default=str(Path.home() / ".agent-workshop" / "boards"))
    parser.add_argument("--allow-opencode", action="store_true")
    parser.add_argument("--approve-code", action="append", default=[], metavar="STEP_ID")
    parser.add_argument("--allow-cloud", action="store_true")
    parser.add_argument("--allow-may-train", action="store_true")
    parser.add_argument("--approve-text-agents", action="store_true",
                        help="Approve all text-only steps served by OpenCode (never code steps)")
    parser.add_argument("--context-root", default=".", help="Folder that spec context_files are read from (default: current directory)")
    parser.add_argument("--allow-main-checkout", action="store_true",
                        help="Let a code step edit a normal checkout directly (default: only disposable worktrees)")
    parser.add_argument("--allow-paid-models", action="store_true",
                        help="Permit paid/subscription-backed OpenCode models (default: free and local only)")
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--cache", action="store_true",
                        help="Reuse identical text replies from ~/.agent-workshop/cache (stored on disk, 7 days)")
    parser.add_argument("--auto-model", action="append", default=[], metavar="MODEL_ID",
                        help="Candidate for steps whose model is \"auto\" (repeatable; paid ids are never picked)")
    parser.add_argument("--dry-run", action="store_true", help="Create the board only; call no model")
    args = parser.parse_args(argv)
    if args.resume:
        board = Board(args.resume)
        if not board.state_path.is_file():
            parser.error("Not a board folder: " + args.resume)
        print("Resuming; re-queued: " + (", ".join(board.reset_failed(args.retry_code)) or "nothing"), flush=True)
    elif args.spec:
        board = create_board(args.root, json.loads(Path(args.spec).read_text(encoding="utf-8")),
                             context_root=args.context_root)
    else:
        parser.error("Provide a spec file or --resume BOARD_DIR")
    print("Board: " + str(board.folder / "board.md"), flush=True)
    if args.dry_run:
        return 0
    summary = run_board(board, Providers(), allow_opencode=args.allow_opencode,
                        allow_cloud=args.allow_cloud, allow_may_train=args.allow_may_train,
                        allow_paid=args.allow_paid_models,
                        approve_text_agents=args.approve_text_agents,
                        allow_main_checkout=args.allow_main_checkout, approved_code=args.approve_code, max_parallel=args.max_parallel,
                        progress=lambda m: print(m, file=sys.stderr, flush=True), ledger=UsageLedger(),
                        auto_models=args.auto_model, stats=RunStats(),
                        cache=ResultCache() if args.cache else None)
    if args.cache:
        summary["cache"] = ResultCache().savings()
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not summary["failed"] and not summary["blocked"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
