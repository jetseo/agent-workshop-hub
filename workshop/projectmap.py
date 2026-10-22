"""Read-only project reading: folder map, key documents, languages, build/test commands, recent commits.

Done by JetAI before the agent starts, so the agent explores less (fewer tokens). Nothing is executed,
secret-looking files are never listed or opened, and every part has a size limit.
"""
import json
import re
import subprocess
from pathlib import Path

from .orchestrator import SECRET_NAME_PARTS

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "env", "dist", "build", "target", "bin", "obj",
                ".idea", ".vscode", ".pytest_cache", ".mypy_cache", ".next", ".gradle", "coverage"}
DOC_NAMES = ("AGENTS.md", "CLAUDE.md", "README.md", "README", "README.rst", "CONTRIBUTING.md")
DOC_CHARS = 700
MAX_FILES = 5000
MAX_LINES = 40
SUMMARY_CHARS = 4000
LANGUAGES = {".py": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
             ".jsx": "JavaScript", ".cs": "C#", ".java": "Java", ".kt": "Kotlin", ".go": "Go", ".rs": "Rust", ".rb": "Ruby",
             ".php": "PHP", ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++", ".swift": "Swift", ".ps1": "PowerShell",
             ".sh": "Shell", ".html": "HTML", ".css": "CSS"}


def _secret(name):
    lowered = name.lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def _git_text(root, *args):
    try:
        done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace")


def list_files(root):
    """Relative file paths (tracked files when it is a git repo, else a bounded walk), without secrets/noise."""
    out = _git_text(root, "ls-files", "-z")
    paths = [p for p in out.split("\0") if p] if out is not None else None
    if paths is None:
        paths = []
        stack = [root]
        while stack and len(paths) < MAX_FILES:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir():
                    if entry.name not in IGNORED_DIRS:
                        stack.append(entry)
                elif entry.is_file():
                    paths.append(entry.relative_to(root).as_posix())
    kept = []
    for path in paths:
        parts = path.split("/")
        if any(p in IGNORED_DIRS for p in parts[:-1]) or _secret(parts[-1]):
            continue
        kept.append(path)
        if len(kept) >= MAX_FILES:
            break
    return sorted(kept)


def _tree(files):
    """Top level: files and folders with counts; second level for folders (capped)."""
    top_files, folders = [], {}
    for path in files:
        parts = path.split("/")
        if len(parts) == 1:
            top_files.append(path)
        else:
            folders.setdefault(parts[0], []).append(parts[1:])
    lines = []
    for name in sorted(folders):
        rest = folders[name]
        lines.append("%s/ (%d files)" % (name, len(rest)))
        subs = {}
        for parts in rest:
            if len(parts) > 1:
                subs[parts[0]] = subs.get(parts[0], 0) + 1
        for sub in sorted(subs)[:6]:
            lines.append("  %s/%s/ (%d)" % (name, sub, subs[sub]))
    lines.extend(top_files[:15])
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + ["... (%d more lines)" % (len(lines) - MAX_LINES)]
    return lines


def _languages(files):
    counts = {}
    for path in files:
        suffix = Path(path).suffix.lower()
        if suffix in LANGUAGES:
            counts[LANGUAGES[suffix]] = counts.get(LANGUAGES[suffix], 0) + 1
    return [name for name, _ in sorted(counts.items(), key=lambda item: -item[1])][:5]


def _read(root, name, limit=60_000):
    try:
        path = root / name
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_bytes()[:limit].decode("utf-8", "replace")
    except OSError:
        return None


def _docs(root, files):
    present = set(files)
    docs = []
    for name in DOC_NAMES:
        if name not in present:
            continue
        text = _read(root, name)
        if text:
            text = re.sub(r"\s+", " ", re.sub(r"```.*?```", " ", text, flags=re.S)).strip()
            docs.append({"file": name, "text": text[:DOC_CHARS]})
    return docs


def _node_manager(present):
    if "pnpm-lock.yaml" in present:
        return "pnpm"
    if "yarn.lock" in present:
        return "yarn"
    if "bun.lockb" in present or "bun.lock" in present:
        return "bun"
    return "npm"


def detect_commands(root, files):
    """Build/test/lint commands guessed from project files. Only suggestions; JetAI does not run them here."""
    present = set(files)
    found = []

    def add(kind, command, source):
        if not any(c["kind"] == kind and c["command"] == command for c in found):
            found.append({"kind": kind, "command": command, "source": source})

    if "package.json" in present:
        try:
            scripts = json.loads(_read(root, "package.json") or "{}").get("scripts", {})
        except ValueError:
            scripts = {}
        if isinstance(scripts, dict):
            manager = _node_manager(present)
            for kind in ("build", "test", "lint"):
                if isinstance(scripts.get(kind), str):
                    add(kind, "%s run %s" % (manager, kind) if kind != "test" else manager + " test", "package.json")
    pyproject = _read(root, "pyproject.toml") or ""
    if "pytest.ini" in present or "[tool.pytest" in pyproject or "conftest.py" in present or "tox.ini" in present:
        add("test", "python -m pytest", "pytest 설정")
    elif any(p.startswith("tests/") and p.endswith(".py") for p in files) or any(Path(p).name.startswith("test_") for p in files):
        add("test", "python -m unittest discover -s tests" if any(p.startswith("tests/") for p in files) else "python -m unittest discover", "tests 폴더")
    if "[tool.ruff" in pyproject or "ruff.toml" in present:
        add("lint", "ruff check .", "ruff 설정")
    makefile = _read(root, "Makefile") or ""
    for target in ("build", "test", "lint"):
        if re.search(r"^%s\s*:" % target, makefile, flags=re.M):
            add(target, "make " + target, "Makefile")
    if any(p.endswith((".sln", ".csproj")) for p in files):
        add("build", "dotnet build", ".NET 프로젝트")
        add("test", "dotnet test", ".NET 프로젝트")
    if "Cargo.toml" in present:
        add("build", "cargo build", "Cargo.toml")
        add("test", "cargo test", "Cargo.toml")
    if "go.mod" in present:
        add("build", "go build ./...", "go.mod")
        add("test", "go test ./...", "go.mod")
    if "pom.xml" in present:
        add("build", "mvn -q compile", "pom.xml")
        add("test", "mvn -q test", "pom.xml")
    if "build.gradle" in present or "build.gradle.kts" in present:
        add("build", "gradle build", "gradle")
        add("test", "gradle test", "gradle")
    return found


def read_project(path):
    root = Path(path).resolve()
    files = list_files(root)
    log = _git_text(root, "log", "-5", "--format=%h %s")
    return {"root": str(root), "file_count": len(files), "empty": not files, "languages": _languages(files),
            "tree": _tree(files), "docs": _docs(root, files), "commands": detect_commands(root, files),
            "commits": [line[:120] for line in (log or "").splitlines()[:5]]}


def render(info):
    """Compact Markdown text for the agent request and the report."""
    if info["empty"]:
        return "프로젝트 요약: 파일이 없는 빈 저장소입니다."
    lines = ["프로젝트 요약 (JetAI가 미리 읽음, 파일 %d개)" % info["file_count"]]
    if info["languages"]:
        lines.append("언어: " + ", ".join(info["languages"]))
    if info["commands"]:
        lines.append("명령 후보: " + "; ".join("%s=`%s`" % (c["kind"], c["command"]) for c in info["commands"]))
    else:
        lines.append("명령 후보: 감지되지 않음(빌드·테스트 방법을 직접 확인하세요)")
    lines.append("구조:")
    lines.extend("  " + line for line in info["tree"])
    for doc in info["docs"]:
        lines.append("%s: %s" % (doc["file"], doc["text"]))
    if info["commits"]:
        lines.append("최근 커밋: " + " | ".join(info["commits"]))
    return "\n".join(lines)[:SUMMARY_CHARS]
