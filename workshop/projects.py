"""Project registry: the projects JetAI may work on, with their commands, branches and permission policy.

Every code run belongs to a registered project and works in a throw-away copy of it (never in the project itself).
The registry only stores settings; removing an entry never touches any files. Nothing here runs a command: detected
and user-defined commands are used later by the fixed, shell-free runners (`checks`), and a user-defined command is
refused here if it is on the never-allow list (`cmdguard`).
"""
import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

from . import cmdguard, projectmap

MAX_PROJECTS = 50
SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,60}")
COMMAND_KINDS = ("build", "test", "run", "lint")
COMMIT_POLICIES = ("never", "ask")                 # "ask": a work-branch commit may be offered, always after the user's approval
COMPUTER_USE_MODES = ("observe", "approval")       # no automatic mode yet: it comes last, after the emergency stop is verified
DEFAULT_POLICY = {"commit": "never", "computer_use": "observe"}
DEV_URL = re.compile(r"https?://(localhost|127\.0\.0\.1|\[::1\])(:\d{1,5})?(/[^\s]*)?")


class ProjectError(Exception):
    """A problem to show to the user as is (Korean message)."""


def _git(root, *args):
    try:
        done = subprocess.run(["git", *args], cwd=str(root), capture_output=True, timeout=15, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.decode("utf-8", "replace").strip() if done.returncode == 0 else ""


def _clean_remote(url):
    return re.sub(r"//[^/@\s]+@", "//", url or "")[:200]       # never keep credentials that may sit in a remote URL


def _slug(text):
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40]
    return base if SLUG.fullmatch(base or "") else "project"


def _now_default():
    return time.time()


class ProjectRegistry:
    def __init__(self, path, *, check_repo, clock=_now_default):
        self.path = Path(path)
        self.check_repo = check_repo                 # raises with a Korean message for unsafe or non-git folders
        self.clock = clock
        self._lock = threading.RLock()

    # ---- storage -------------------------------------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    # ---- detection (read only) -----------------------------------------------------------------------------
    @staticmethod
    def detect(root):
        info = projectmap.read_project(root)
        branch = _git(root, "branch", "--show-current")
        default = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").replace("origin/", "", 1) or branch
        return {"remote": _clean_remote(_git(root, "remote", "get-url", "origin")), "default_branch": default,
                "languages": info["languages"], "instructions": [d["file"] for d in info["docs"]],
                "detected_commands": info["commands"]}

    # ---- queries -------------------------------------------------------------------------------------------
    def list(self):
        with self._lock:
            projects = list(self._load().values())
        return sorted(projects, key=lambda p: (-(p.get("last_task") or {}).get("at", 0), p["name"].lower()))

    def get(self, project_id):
        with self._lock:
            project = self._load().get(project_id)
        if project is None:
            raise ProjectError("등록되지 않은 프로젝트입니다.")
        return project

    def find_by_path(self, path):
        try:
            resolved = str(Path(path).expanduser().resolve(strict=True))
        except (OSError, ValueError, RuntimeError):
            return None
        with self._lock:
            for project in self._load().values():
                if os.path.normcase(project["path"]) == os.path.normcase(resolved):
                    return project
        return None

    def command_overrides(self, path):
        """{"build": "...", "test": "..."} the user set for the project at `path` (empty when none or unregistered)."""
        project = self.find_by_path(path)
        return {k: v for k, v in (project or {}).get("commands", {}).items() if v} if project else {}

    # ---- changes -------------------------------------------------------------------------------------------
    def add(self, path, name=None):
        try:
            root = self.check_repo(path)
        except Exception as error:                       # the folder rules live in one place (CodeRuns.check_repo)
            raise ProjectError(str(error)) from None
        existing = self.find_by_path(root)
        if existing is not None:
            return existing
        with self._lock:
            data = self._load()
            if len(data) >= MAX_PROJECTS:
                raise ProjectError("등록할 수 있는 프로젝트는 최대 %d개입니다. 쓰지 않는 것을 지우세요." % MAX_PROJECTS)
            title = self._name(name or root.name)
            base, project_id, n = _slug(title), None, 1
            while project_id is None or project_id in data:
                project_id = base if n == 1 else "%s-%d" % (base[:36], n)
                n += 1
            now = self.clock()
            project = {"id": project_id, "name": title, "path": str(root), "work_branch": "", "dev_url": "", "allowed_apps": [], "notes": "",
                       "commands": {k: None for k in COMMAND_KINDS}, "policy": dict(DEFAULT_POLICY), "created": now, "updated": now,
                       "last_task": None, **self.detect(root)}
            data[project_id] = project
            self._save(data)
            return project

    @staticmethod
    def _name(value):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 60:
            raise ProjectError("이름은 1~60자로 쓰세요.")
        return re.sub(r"\s+", " ", value.strip())

    def update(self, project_id, **fields):
        with self._lock:
            data = self._load()
            project = data.get(project_id)
            if project is None:
                raise ProjectError("등록되지 않은 프로젝트입니다.")
            for key, value in fields.items():
                if key == "name":
                    project["name"] = self._name(value)
                elif key == "work_branch":
                    if value and not BRANCH.fullmatch(str(value)) or ".." in str(value or ""):
                        raise ProjectError("작업 브랜치 이름이 올바르지 않습니다(영문·숫자·.·_·-·/ 만).")
                    project["work_branch"] = str(value or "")
                elif key == "dev_url":
                    if value and not DEV_URL.fullmatch(str(value)):
                        raise ProjectError("개발 서버 주소는 이 PC(localhost·127.0.0.1)의 http(s) 주소만 쓸 수 있습니다.")
                    project["dev_url"] = str(value or "")
                elif key == "notes":
                    project["notes"] = str(value or "")[:500]
                elif key == "allowed_apps":
                    if not isinstance(value, list) or len(value) > 10 or any(not isinstance(a, str) or not 1 <= len(a.strip()) <= 60 for a in value):
                        raise ProjectError("허용 앱은 이름 최대 10개(각 1~60자)로 적으세요.")
                    project["allowed_apps"] = [a.strip() for a in value]
                elif key == "commands":
                    project["commands"] = self._commands(value, project["commands"])
                elif key == "policy":
                    project["policy"] = self._policy(value, project["policy"])
                else:
                    raise ProjectError("바꿀 수 없는 항목입니다: " + str(key))
            project["updated"] = self.clock()
            self._save(data)
            return project

    @staticmethod
    def _commands(value, current):
        if not isinstance(value, dict):
            raise ProjectError("명령 설정이 올바르지 않습니다.")
        result = dict(current)
        for kind, command in value.items():
            if kind not in COMMAND_KINDS:
                raise ProjectError("알 수 없는 명령 종류입니다: " + str(kind))
            if command in (None, ""):
                result[kind] = None
                continue
            if not isinstance(command, str):
                raise ProjectError("명령은 글로 적으세요.")
            level, note = cmdguard.classify(command)
            if level == "never":
                raise ProjectError("이 명령은 쓸 수 없습니다: " + note)
            result[kind] = command.strip()
        return result

    @staticmethod
    def _policy(value, current):
        if not isinstance(value, dict):
            raise ProjectError("권한 정책이 올바르지 않습니다.")
        result = dict(current)
        if "commit" in value:
            if value["commit"] not in COMMIT_POLICIES:
                raise ProjectError("커밋 정책은 never 또는 ask 만 가능합니다.")
            result["commit"] = value["commit"]
        if "computer_use" in value:
            if value["computer_use"] not in COMPUTER_USE_MODES:
                raise ProjectError("화면 조작 모드는 observe 또는 approval 만 가능합니다(자동 모드는 아직 없습니다).")
            result["computer_use"] = value["computer_use"]
        unknown = set(value) - {"commit", "computer_use"}
        if unknown:
            raise ProjectError("알 수 없는 정책 항목입니다: " + ", ".join(sorted(unknown)))
        return result

    def refresh(self, project_id):
        """Detect again (languages, instruction files, commands, remote, branch); the user's own settings stay."""
        with self._lock:
            data = self._load()
            project = data.get(project_id)
            if project is None:
                raise ProjectError("등록되지 않은 프로젝트입니다.")
            try:
                root = self.check_repo(project["path"])
            except Exception as error:
                raise ProjectError(str(error)) from None
            project.update(self.detect(root))
            project["updated"] = self.clock()
            self._save(data)
            return project

    def touch(self, project_id, run_id):
        with self._lock:
            data = self._load()
            if project_id in data:
                data[project_id]["last_task"] = {"id": run_id, "at": self.clock()}
                self._save(data)

    def remove(self, project_id):
        """Forget the project. Its folder and any finished runs are left exactly as they are."""
        with self._lock:
            data = self._load()
            if data.pop(project_id, None) is None:
                raise ProjectError("등록되지 않은 프로젝트입니다.")
            self._save(data)
