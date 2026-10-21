"""Disposable git worktrees for code steps. Never commits, pushes or merges.

  python -m workshop.worktree remove REPO WORKTREE_PATH   # delete a worktree and its branch
"""
import argparse
import subprocess
import sys
from pathlib import Path

MAX_DIFF_CHARS = 50_000


def default_base():
    return Path.home() / ".agent-workshop" / "worktrees"


def _git(*args, cwd, check=True):
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, check=False)
    if check and done.returncode != 0:
        raise RuntimeError("git " + " ".join(args[:2]) + " failed: " + (done.stderr.strip() or done.stdout.strip())[-300:])
    return done.stdout


def is_repo(path):
    return (Path(path) / ".git").exists()


def create(repo, base, board_id, step_id):
    """New worktree at <base>/<board_id>/<step_id> on branch workshop/<board8>-<step>, from HEAD."""
    repo = Path(repo).resolve()
    if not is_repo(repo):
        raise ValueError("Not a git repository: " + str(repo))
    _git("rev-parse", "--verify", "HEAD", cwd=repo)  # needs at least one commit
    path = Path(base) / board_id / step_id
    if path.exists():
        raise ValueError("Worktree already exists (remove it first): " + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    branch = "workshop/" + board_id[:8] + "-" + step_id
    _git("worktree", "add", "-b", branch, str(path), "HEAD", cwd=repo)
    return path, branch


def collect_diff(path):
    """Stage everything inside the worktree (no commit) and return stat + diff vs HEAD."""
    _git("add", "-A", cwd=path)
    stat = _git("diff", "--cached", "--stat", "HEAD", cwd=path).strip()
    diff = _git("diff", "--cached", "HEAD", cwd=path)
    truncated = len(diff) > MAX_DIFF_CHARS
    return {"changed": bool(stat), "stat": stat, "diff": diff[:MAX_DIFF_CHARS], "truncated": truncated}


def remove(repo, path):
    path = Path(path).resolve()
    branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=path).strip()
    _git("worktree", "remove", "--force", str(path), cwd=repo)
    if branch.startswith("workshop/"):
        _git("branch", "-D", branch, cwd=repo)
    return branch


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manage disposable workshop worktrees")
    sub = parser.add_subparsers(dest="command", required=True)
    rem = sub.add_parser("remove")
    rem.add_argument("repo")
    rem.add_argument("path")
    args = parser.parse_args(argv)
    try:
        print("Removed worktree and branch: " + remove(args.repo, args.path))
    except (RuntimeError, ValueError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
