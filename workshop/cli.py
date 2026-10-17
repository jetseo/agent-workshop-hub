"""Queue CLI: python3 -m workshop.cli [--db PATH] <command>."""
import argparse
import json
from pathlib import Path
from .queue import Queue

def main():
    p = argparse.ArgumentParser(description="Agent Workshop Hub")
    p.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    sub = p.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add")
    add.add_argument("kind", choices=["plan", "code", "image", "qa"])
    add.add_argument("title")
    add.add_argument("--workspace", help="Existing Git worktree or repository directory")
    sub.add_parser("list")
    sub.add_parser("claim")
    sub.add_parser("recover")
    finish = sub.add_parser("finish")
    finish.add_argument("id")
    finish.add_argument("--failed", action="store_true")
    approve = sub.add_parser("approve")
    approve.add_argument("id")
    args = p.parse_args()
    q = Queue(args.db)
    if args.command == "add":
        result = {"id": q.add(args.kind, args.title, {"workspace": args.workspace} if args.workspace else {})}
    elif args.command == "list":
        result = q.list()
    elif args.command == "claim":
        result = q.claim()
    elif args.command == "recover":
        result = {"marked_failed": q.recover()}
    elif args.command == "finish":
        result = {"state": q.finish(args.id, success=not args.failed)}
    else:
        q.approve(args.id)
        result = {"state": "done"}
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
