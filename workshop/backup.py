"""Export / import the data folder: python -m workshop.backup export|import.

Keys (secrets.json) and the terminal start-up key are left out unless --include-secrets is given, because a backup
file is easy to leave in a synced folder. Import never overwrites an existing file unless --force."""
import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

DATA = Path.home() / ".agent-workshop"
SECRET_NAMES = {"secrets.json", "terminal.key"}
SKIP_DIRS = {"logs", "worktrees"}           # logs are large and local; worktrees are disposable git checkouts
MAX_ENTRY_BYTES = 200 * 1024 * 1024


def _files(root, include_secrets):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in SKIP_DIRS or path.suffix == ".tmp":
            continue
        if path.name in SECRET_NAMES and not include_secrets:
            continue
        yield path, relative


def export(root, out, include_secrets=False):
    root, out = Path(root), Path(out)
    if not root.is_dir():
        raise FileNotFoundError("Data folder not found: " + str(root))
    if out.exists():
        raise FileExistsError("Refusing to overwrite " + str(out))
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, relative in _files(root, include_secrets):
            archive.write(path, relative.as_posix())
            count += 1
        archive.writestr("BACKUP.json", json.dumps({"made": time.strftime("%Y-%m-%d %H:%M:%S"), "files": count,
                                                    "includes_secrets": bool(include_secrets)}))
    if out.exists() and sys.platform != "win32":
        out.chmod(0o600)
    return {"files": count, "includes_secrets": bool(include_secrets), "path": str(out)}


def _safe_target(root, name):
    target = (root / name).resolve()
    if root.resolve() not in target.parents:
        raise ValueError("Unsafe path in backup: " + name)
    return target


def restore(archive_path, root, force=False, include_secrets=False):
    root = Path(root)
    restored, skipped = [], []
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            name = info.filename
            if name == "BACKUP.json" or info.is_dir():
                continue
            if name.startswith("/") or ".." in Path(name).parts or ":" in name:
                raise ValueError("Unsafe path in backup: " + name)
            if info.file_size > MAX_ENTRY_BYTES:
                raise ValueError("Entry too large: " + name)
            if Path(name).name in SECRET_NAMES and not include_secrets:
                skipped.append(name)
                continue
            target = _safe_target(root, name)
            if target.exists() and not force:
                skipped.append(name)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            if Path(name).name in SECRET_NAMES and sys.platform != "win32":
                target.chmod(0o600)
            restored.append(name)
    return {"restored": restored, "skipped_existing_or_secret": skipped}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up or restore JetAI data (chats, services, usage, boards)")
    sub = parser.add_subparsers(dest="command", required=True)
    e = sub.add_parser("export")
    e.add_argument("--out", help="zip file to create (default: jetai-backup-<date>.zip in the current folder)")
    e.add_argument("--include-secrets", action="store_true", help="also include saved keys (keep the file private!)")
    i = sub.add_parser("import")
    i.add_argument("file")
    i.add_argument("--force", action="store_true", help="overwrite files that already exist")
    i.add_argument("--include-secrets", action="store_true")
    parser.add_argument("--data", default=str(DATA), help="data folder (default ~/.agent-workshop)")
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            out = args.out or "jetai-backup-" + time.strftime("%Y%m%d-%H%M%S") + ".zip"
            result = export(args.data, out, args.include_secrets)
            if args.include_secrets:
                print("WARNING: this file contains your saved keys. Do not share or sync it.", file=sys.stderr)
        else:
            result = restore(args.file, args.data, args.force, args.include_secrets)
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
