"""Provider onboarding. Read-only checks by default; keys stay in environment variables.

  python -m workshop.onboard list
  python -m workshop.onboard add NAME --base-url URL [--key-env VAR] [--policy P] [--rpm N]
  python -m workshop.onboard remove NAME
  python -m workshop.onboard check            # local only: opencode models, key variables set?
  python -m workshop.onboard test NAME MODEL --yes   # one tiny request, only after consent
"""
import argparse
import os
import shutil
import subprocess
import sys

from .chat import chat
from .providers import Providers, POLICIES


def _opencode(args):
    binary = shutil.which("opencode")
    if binary is None:
        return None
    done = subprocess.run([binary, *args], capture_output=True, text=True, timeout=30, check=False)
    return done.stdout.strip() if done.returncode == 0 else "(exit " + str(done.returncode) + ")"


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent Workshop provider onboarding")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    add = sub.add_parser("add")
    add.add_argument("name")
    add.add_argument("--base-url", required=True)
    add.add_argument("--key-env")
    add.add_argument("--policy", default="unknown", choices=POLICIES)
    add.add_argument("--rpm", type=int, default=0)
    rem = sub.add_parser("remove")
    rem.add_argument("name")
    test = sub.add_parser("test")
    test.add_argument("name")
    test.add_argument("model")
    test.add_argument("--yes", action="store_true", help="Consent to one small request to this provider")
    parser.add_argument("--file", help="providers.json path (default ~/.agent-workshop/providers.json)")
    args = parser.parse_args(argv)
    registry = Providers(args.file)
    try:
        if args.command == "add":
            registry.add(args.name, base_url=args.base_url, key_env=args.key_env,
                         data_policy=args.policy, rpm=args.rpm)
            print("Added " + args.name + ". Set the key yourself: " +
                  (args.key_env or "(no key needed)"))
        elif args.command == "remove":
            registry.remove(args.name)
            print("Removed " + args.name)
        elif args.command == "list":
            for name, p in registry.load().items():
                key = "n/a" if not p["key_env"] else ("set" if os.environ.get(p["key_env"]) else "NOT SET")
                print(name + "  " + p["base_url"] + "  policy=" + p["data_policy"] +
                      "  rpm=" + str(p["rpm"]) + "  key(" + str(p["key_env"]) + ")=" + key)
        elif args.command == "check":
            out = _opencode(["models"])
            print("opencode: " + ("not installed" if out is None else str(len(out.splitlines())) + " models listed"))
            for name, p in registry.load().items():
                ok = not p["key_env"] or bool(os.environ.get(p["key_env"]))
                print(("[OK] " if ok else "[--] ") + name + (" " if ok else " key variable not set: " + p["key_env"]))
        elif args.command == "test":
            provider = registry.get(args.name)
            if provider is None:
                raise ValueError("Unknown provider")
            if not args.yes:
                raise PermissionError("Add --yes to send one tiny request (data policy: " +
                                      provider["data_policy"] + ")")
            chat(provider, args.model, [{"role": "user", "content": "Reply with: ok"}], max_tokens=5)
            print("OK: provider responded")
    except (ValueError, PermissionError, RuntimeError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
