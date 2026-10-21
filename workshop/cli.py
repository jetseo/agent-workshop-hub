"""Queue CLI: python3 -m workshop.cli [--db PATH] <command>."""
import argparse
import json
from pathlib import Path
from .queue import Queue

def main():
    p = argparse.ArgumentParser(description="JetAI")
    p.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("kind", choices=["plan", "code", "qa"])
    add.add_argument("title")
    add.add_argument("--workspace", help="Existing Git worktree or repository directory")
    add.add_argument("--model", help="provider/name as listed by `opencode models` (local or -free only; booked in the usage ledger)")
    add.add_argument("--skip-execution-approval", action="store_true",
                     help="Unsafe: allow immediate execution of newly queued code tasks")
    sub.add_parser("list")
    claim = sub.add_parser("claim")
    claim.add_argument("--unsafe-manual", action="store_true", help="Advanced queue administration only")
    sub.add_parser("recover")
    authorize = sub.add_parser("authorize")
    authorize.add_argument("id")
    finish = sub.add_parser("finish")
    finish.add_argument("id")
    finish.add_argument("--failed", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("id")
    args = p.parse_args()
    q = Queue(args.db)
    if args.command == "add":
        payload = {"workspace": args.workspace} if args.workspace else {}
        if args.model:
            payload["model"] = args.model
        result = {"id": q.add(args.kind, args.title, payload,
                              require_approval=(args.kind == "code" and not args.skip_execution_approval))}
    elif args.command == "list":
        result = q.list()
    elif args.command == "claim":
        if not args.unsafe_manual:
            p.error("Manual claim requires --unsafe-manual; normal execution uses the worker")
        result = q.claim()
    elif args.command == "recover":
        result = {"marked_failed": q.recover()}
    elif args.command == "authorize":
        q.authorize(args.id)
        result = {"state": "queued"}
    elif args.command == "finish":
        result = {"state": q.finish(args.id, success=not args.failed)}
    else:
        q.approve(args.id)
        result = {"state": "done"}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
