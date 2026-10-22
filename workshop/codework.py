"""Code work: let a coding agent (OpenCode) change files on this PC, safely, and show the result as a diff first.

The flow the app offers:  choose a repository folder + describe the task + (auto) model  ->  JetAI makes a throw-away
git worktree of HEAD  ->  OpenCode runs INSIDE it  ->  you read the diff  ->  "apply" copies the patch into your
folder (uncommitted, you commit it) or "discard" deletes everything. Nothing is ever committed, pushed or merged here.

Safety design (each point is covered by tests):
  * explicit start only; one run at a time; a time limit; cancel kills the whole process tree;
  * the agent only ever sees the worktree (HEAD of your repo), never your working folder, and OpenCode's
    `external_directory` access is denied; web fetch is denied; shell commands are denied except an allow-list
    (git status/diff, running tests, python/node version checks);
  * OpenCode runs with a JetAI-owned, empty config/data directory: your own OpenCode plugins, skills and - above all -
    your paid credentials (OpenCode Zen key, OpenAI login) are NOT available to it;
  * the process gets a minimal environment (no tokens from your shell); a service key is passed in one variable only;
  * models: free or local only; a paid model needs the explicit `confirm_cost`; OpenCode Zen is limited to `-free` ids;
  * apply = `git apply` of the saved patch after `--check`, never a commit.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from . import adapters, checks, cmdguard, codereport, codereview, modelpick, projectmap, usage, worktree

CHECK_TIMEOUT = 300                                             # seconds per build/test command
MAX_PROMPT = 8000
MAX_RUNTIME = 20 * 60
MAX_EVENTS_FILE = 20_000_000
MAX_SHOWN_PATCH = 400_000
KEEP_RUNS = 30
RUN_ID = re.compile(r"[0-9a-f]{12}")
MODEL_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}/[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,200}")
PROVIDER_ID = "jetai"
KEY_VARIABLE = "JETAI_PROVIDER_KEY"
WINDOWS_ENV_KEEP = ("SYSTEMROOT", "SystemRoot", "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE",
                    "HOMEPATH", "LOCALAPPDATA", "APPDATA", "PROGRAMDATA", "PROGRAMFILES", "ProgramFiles", "ProgramFiles(x86)", "PATH",
                    "Path", "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "OS", "windir", "LANG", "HOME", "USER", "LOGNAME", "SHELL", "TERM")
# Shell commands the agent may run (everything else is denied). It can still read, search and edit inside the worktree.
BASH_ALLOW = ["git status*", "git diff*", "git log*", "git show*", "python --version", "python -m unittest*", "python -m pytest*",
              "python3 -m unittest*", "python3 -m pytest*", "pytest*", "npm test*", "npm run test*", "node --version", "dir", "ls", "ls *", "type *", "cat *"]
NOISE_PATHS = ["**/__pycache__/**", "**/*.pyc", "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**", "**/node_modules/**",
               "**/.venv/**", "**/.DS_Store", "**/.omo/**"]
STATUS_LABEL = {"running": "실행 중", "review": "검토 대기", "nochange": "변경 없음", "failed": "실패", "cancelled": "취소됨",
                "applied": "적용됨", "discarded": "버림"}


class CodeWorkError(Exception):
    """A problem to show to the user as is (Korean message)."""


def home_dir(providers):
    return providers.path.parent


# ---- OpenCode launch ----------------------------------------------------------------------------------------------

def clean_environment(base_env, opencode_home, config_json, key=None):
    """A minimal environment: no tokens from the user's shell, OpenCode's own directories inside JetAI's folder."""
    env = {k: v for k, v in base_env.items() if k in WINDOWS_ENV_KEEP}
    for sub, variable in (("config", "XDG_CONFIG_HOME"), ("data", "XDG_DATA_HOME"), ("state", "XDG_STATE_HOME"), ("cache", "XDG_CACHE_HOME")):
        directory = Path(opencode_home) / sub
        directory.mkdir(parents=True, exist_ok=True)
        env[variable] = str(directory)
    env["OPENCODE_CONFIG_CONTENT"] = config_json
    env["OPENCODE_DISABLE_CLAUDE_CODE"] = "1"
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "1"
    if key:
        env[KEY_VARIABLE] = key
    return env


def build_config(model_ref, provider=None, extra_allow=()):
    """OpenCode config (passed as JSON in an environment variable, so no file is written into the repository)."""
    config = {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled",
              "permission": {"edit": "allow", "webfetch": "deny", "external_directory": "deny", "doom_loop": "deny",
                             "bash": {"*": "deny", **{pattern: "allow" for pattern in [*BASH_ALLOW, *extra_allow]}}}}
    if provider is not None:
        name = model_ref.split("/", 1)[1]
        options = {"baseURL": provider["base_url"]}
        if provider.get("api_key"):
            options["apiKey"] = "{env:%s}" % KEY_VARIABLE                   # the key itself never appears in the config
        config["provider"] = {PROVIDER_ID: {"npm": "@ai-sdk/openai-compatible", "name": "JetAI service", "options": options,
                                            "models": {name: {"name": name, "tool_call": True}}}}
    return json.dumps(config, ensure_ascii=False)


def opencode_command(binary, model_arg, prompt, session=None):
    command = [binary, "run", "--pure", "--format", "json", "-m", model_arg]
    if session:
        command += ["-s", session]                                       # carry on in the same conversation after an approval
    return command + [prompt]


def _git_bytes(*args, cwd):
    """git output as raw bytes, with file names left unquoted (core.quotepath=false) so Korean paths stay readable."""
    done = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True, stdin=subprocess.DEVNULL, check=False)
    if done.returncode != 0:
        raise RuntimeError("git " + " ".join(args[:2]) + " failed: " + (done.stderr.decode("utf-8", "replace").strip() or "")[-300:])
    return done.stdout


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, check=False)
    else:
        proc.kill()


# ---- summaries of OpenCode's JSON events --------------------------------------------------------------------------

def summarise(event):
    """One short line for the screen from an OpenCode `--format json` event, or None for noise."""
    part = event.get("part") or {}
    kind = event.get("type")
    if kind == "tool_use":
        state = part.get("state") or {}
        tool, status = part.get("tool", "tool"), state.get("status", "")
        data = state.get("input") or {}
        target = data.get("filePath") or data.get("pattern") or data.get("command") or data.get("description") or ""
        mark = {"completed": "✓", "error": "✗"}.get(status, "…")
        text = "%s %s %s" % (mark, tool, str(target)[:140])
        if status == "error":
            text += " — " + str(state.get("error", ""))[:120]
        return text.strip()
    if kind == "text" and part.get("text"):
        return "💬 " + re.sub(r"\s+", " ", part["text"])[:200]
    return None


# ---- the manager --------------------------------------------------------------------------------------------------

class CodeRuns:
    def __init__(self, providers, *, find_binary=adapters.find_opencode, popen=subprocess.Popen, clock=time.time, max_runtime=MAX_RUNTIME,
                 worktree_base=None, info=None, command_fn=opencode_command, check_timeout=CHECK_TIMEOUT):
        self.providers = providers
        self.command_fn = command_fn
        self.home = home_dir(providers)
        self.runs_dir = self.home / "coderuns"
        self.opencode_home = self.home / "opencode"
        self.worktree_base = Path(worktree_base) if worktree_base else self.home / "worktrees"
        self.find_binary, self.popen, self.clock, self.max_runtime = find_binary, popen, clock, max_runtime
        self.info = info
        self.check_timeout = check_timeout
        self.review_fn = codereview.review
        self.rules = cmdguard.ProjectRules(self.home / "command-rules.json")
        self._lock = threading.RLock()
        self._procs = {}
        self._threads = {}
        self._tails = {}
        self._cancel_flags = {}
        self._timed_out = set()

    # -- storage ------------------------------------------------------------------------------------------------
    def _path(self, run_id):
        if not RUN_ID.fullmatch(run_id or ""):
            raise CodeWorkError("알 수 없는 작업입니다.")
        return self.runs_dir / run_id

    def _read(self, run_id):
        path = self._path(run_id) / "run.json"
        for attempt in range(6):                  # the screen polls while the run thread saves: a read can hit the swap moment
            try:
                with self._lock:
                    return json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                break
            except (OSError, ValueError):
                time.sleep(0.05)
        raise CodeWorkError("알 수 없는 작업입니다.")

    def _save(self, run):
        with self._lock:
            directory = self._path(run["id"])
            directory.mkdir(parents=True, exist_ok=True)
            tmp = directory / "run.tmp"
            tmp.write_text(json.dumps(run, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(tmp, directory / "run.json")
                    self._write_report(run, directory)
                    return
                except PermissionError:
                    if attempt == 4:
                        raise
                    time.sleep(0.05)

    # -- status -------------------------------------------------------------------------------------------------
    def environment(self):
        binary = self.find_binary()
        return {"opencode": bool(binary), "opencode_path": binary or "", "isolated_home": str(self.opencode_home)}

    def list(self):
        runs = []
        for path in sorted(self.runs_dir.glob("*/run.json"), key=lambda p: p.stat().st_mtime, reverse=True) if self.runs_dir.exists() else []:
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            runs.append(self._summary(run))
        return runs[:KEEP_RUNS]

    @staticmethod
    def _summary(run):
        keys = ("id", "repo", "prompt", "model", "status", "started", "finished", "error", "files", "stat", "applied_at", "undone_at", "tier", "repo_was_dirty", "project", "blocked", "approvals", "checks", "review")
        return {k: run.get(k) for k in keys}

    @staticmethod
    def _write_report(run, directory):
        """report.md and metrics.json beside run.json once a run has stopped; a report problem never breaks the run."""
        if run.get("status") == "running":
            return
        try:
            text, metrics = codereport.build(run, directory / "events.jsonl")
            (directory / "report.md").write_text(text, encoding="utf-8")
            (directory / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
        except (OSError, ValueError, KeyError):
            pass

    def get(self, run_id):
        run = self._read(run_id)
        detail = self._summary(run)
        detail["activity"] = list(self._tails.get(run_id) or run.get("activity", []))[-40:]
        report = self._path(run_id) / "report.md"
        if report.exists():
            detail["report"] = report.read_text(encoding="utf-8", errors="replace")[:20000]
        patch = self._path(run_id) / "patch.diff"
        if patch.exists():
            text = patch.read_text(encoding="utf-8", errors="replace")
            detail["patch"] = text[:MAX_SHOWN_PATCH]
            detail["patch_truncated"] = len(text) > MAX_SHOWN_PATCH
        return detail

    # -- model choice -------------------------------------------------------------------------------------------
    def model_options(self):
        """Models the user can pick for code work, each with an honest note about agent suitability."""
        options = []
        for name, entry in self.providers.load().items():
            if entry["data_policy"] != "local" and not entry.get("consented"):
                continue
            for model in entry.get("models", []):
                options.append(self._describe(name, entry, model))
        return options

    def _describe(self, name, entry, model):
        tier = usage.tier_for(entry, model)
        cap = modelpick.capability(model)
        size = modelpick._size_b(model)
        warn = ""
        if tier == "local" and 0 < size <= 15:
            warn = "작은 로컬 모델은 파일을 고치는 도구 호출을 자주 틀립니다(시험에서 확인). 더 큰 모델을 권합니다."
        elif tier == "other":
            warn = "요금이 나올 수 있는 모델입니다."
        return {"ref": name + "/" + model, "tier": tier, "level": cap["level"], "warn": warn}

    def _resolve_model(self, model_ref, confirm_cost):
        """-> (model_arg for OpenCode, provider dict or None, tier). Raises CodeWorkError for anything not allowed."""
        if not isinstance(model_ref, str) or not MODEL_REF.fullmatch(model_ref):
            raise CodeWorkError("모델을 선택하세요.")
        name, _, model = model_ref.partition("/")
        if name == "opencode":                                          # OpenCode's own catalogue: only ids ending in -free
            if not model.endswith("-free"):
                raise CodeWorkError("OpenCode의 유료 모델은 쓸 수 없습니다. 이름이 -free로 끝나는 모델만 허용됩니다.")
            return model_ref, None, "free"
        provider = self.providers.get(name)
        if provider is None:
            raise CodeWorkError("연결되지 않은 서비스입니다: " + name)
        if provider["data_policy"] != "local" and not provider.get("consented"):
            raise CodeWorkError("이 서비스로 코드를 보내려면 연결할 때 동의가 필요합니다.")
        tier = usage.tier_for(provider, model)
        if tier == "other" and confirm_cost is not True:
            raise CodeWorkError("요금이 나올 수 있는 모델입니다. 비용을 확인하고 체크해야 실행됩니다.")
        return PROVIDER_ID + "/" + model, provider, tier

    def auto_model(self, prompt):
        """Best local/free model for agent work: must look capable (level >= 2); small local models are never chosen on their own."""
        from . import arbiter, router
        candidates = []
        for option in self.model_options():
            if option["tier"] in ("local", "free") and option["level"] >= 2 and not option["warn"]:
                candidates.append(option["ref"])
        if not candidates:
            raise CodeWorkError("자동으로 고를 만한 모델이 없습니다(코드 작업에는 중간 이상 성능의 무료·로컬 모델이 필요합니다). 모델을 직접 고르거나 무료 서비스를 연결하세요.")
        level = min(3, arbiter.classify(prompt)["level"] + 1)           # agent work is harder than the same question in chat
        local = {ref for ref in candidates if (self.providers.get(ref.split("/")[0]) or {}).get("data_policy") == "local"}
        decision = router.route_task({"type": "code", "level": level}, candidates, tier_fn=lambda ref: "local" if ref in local else "free")
        return decision["model"]

    # -- start --------------------------------------------------------------------------------------------------
    @staticmethod
    def check_repo(path):
        try:
            repo = Path(path).expanduser().resolve(strict=True)
        except (OSError, ValueError, RuntimeError):
            raise CodeWorkError("폴더를 찾을 수 없습니다.") from None
        if not repo.is_dir() or not worktree.is_repo(repo):
            raise CodeWorkError("git 저장소 폴더가 아닙니다(.git이 있는 폴더를 고르세요).")
        home = Path.home().resolve()
        if repo.parent == repo or repo == home:
            raise CodeWorkError("드라이브 전체나 홈 폴더 전체는 쓸 수 없습니다. 프로젝트 폴더를 고르세요.")
        if home / ".agent-workshop" in (repo, *repo.parents):
            raise CodeWorkError("JetAI의 자료 폴더는 쓸 수 없습니다.")
        if os.name == "nt" and str(repo).lower().startswith(os.environ.get("WINDIR", "C:\\Windows").lower()):
            raise CodeWorkError("시스템 폴더는 쓸 수 없습니다.")
        return repo

    def start(self, repo_path, prompt, model_ref="auto", confirm_cost=False):
        with self._lock:
            if any(t.is_alive() for t in self._threads.values()):
                raise CodeWorkError("다른 코드 작업이 실행 중입니다. 끝나거나 취소한 뒤 시작하세요.")
            binary = self.find_binary()
            if not binary:
                raise CodeWorkError("OpenCode를 찾지 못했습니다. 먼저 설치하세요(npm i -g opencode-ai).")
            repo = self.check_repo(repo_path)
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT:
                raise CodeWorkError("요청은 1~%d자로 써 주세요." % MAX_PROMPT)
            if model_ref in (None, "", "auto"):
                model_ref = self.auto_model(prompt)
            model_arg, provider, tier = self._resolve_model(model_ref, confirm_cost)
            run_id = uuid.uuid4().hex[:12]
            try:
                base_commit = worktree._git("rev-parse", "HEAD", cwd=repo).strip()
                path, branch = worktree.create(repo, self.worktree_base, "code", run_id)
            except (RuntimeError, ValueError) as error:
                raise CodeWorkError("작업용 폴더를 만들지 못했습니다: " + str(error)[:200]) from None
            dirty = bool(worktree._git("status", "--porcelain", cwd=repo, check=False).strip())
            run = {"id": run_id, "repo": str(repo), "prompt": prompt.strip(), "model": model_ref, "tier": tier, "status": "running",
                   "started": self.clock(), "finished": None, "error": "", "worktree": str(path), "branch": branch, "base": base_commit,
                   "files": [], "stat": "", "activity": [], "repo_was_dirty": dirty,
                   "cost_ok": confirm_cost is True, "session": "", "once_allow": [], "blocked": [], "approvals": []}
            self._save(run)
            config = build_config(model_ref, provider, self.rules.get(repo))
            env = clean_environment(os.environ, self.opencode_home, config, key=(provider or {}).get("api_key"))
            try:
                run["project"] = projectmap.render(projectmap.read_project(path))
            except Exception:                                   # reading is a help, never a reason to refuse the work
                run["project"] = ""
            self._save(run)
            request = run["prompt"] + ("\n\n" + run["project"] if run["project"] else "")
            command = self.command_fn(binary, model_arg, request)
            thread = threading.Thread(target=self._run, args=(run, command, env), name="code-" + run_id, daemon=True)
            self._threads[run_id] = thread
            thread.start()
            return self._summary(run)

    def _run(self, run, command, env):
        run_id, directory = run["id"], self._path(run["id"])
        tail = self._tails.setdefault(run_id, [])
        deadline = self.clock() + self.max_runtime
        try:
            with open(directory / "events.jsonl", "ab") as events:
                kwargs = {}
                if os.name == "nt":
                    kwargs["creationflags"] = 0x08000000                  # CREATE_NO_WINDOW
                proc = self.popen(command, cwd=run["worktree"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, **kwargs)
                self._procs[run_id] = proc
                if self._cancel_flags.get(run_id):                       # cancel was pressed before the process existed
                    kill_tree(proc)
                watchdog = threading.Thread(target=self._watch, args=(proc, deadline, run_id), daemon=True)
                watchdog.start()
                size = 0
                for raw in iter(proc.stdout.readline, b""):
                    size += len(raw)
                    if size <= MAX_EVENTS_FILE:
                        events.write(raw)
                    try:
                        parsed = json.loads(raw.decode("utf-8", "replace"))
                        if isinstance(parsed, dict) and isinstance(parsed.get("sessionID"), str) and not run.get("session"):
                            run["session"] = parsed["sessionID"][:80]
                        line = summarise(parsed)
                    except ValueError:
                        line = raw.decode("utf-8", "replace").strip()[:200] or None
                    if line:
                        tail.append(line)
                        del tail[:-120]
                proc.wait()
            self._finish(run, proc.returncode, run_id in self._timed_out, bool(self._cancel_flags.get(run_id)))
        except Exception as error:                                         # the UI must always get an end state
            run["status"], run["error"], run["finished"] = "failed", "실행 중 오류: " + str(error)[:200], self.clock()
            self._save(run)
        finally:
            self._procs.pop(run_id, None)

    def _watch(self, proc, deadline, run_id):
        while proc.poll() is None:
            if self.clock() >= deadline:
                self._timed_out.add(run_id)
                kill_tree(proc)
                return
            time.sleep(0.2)

    def _finish(self, run, code, timed_out, cancelled):
        run["finished"] = self.clock()
        run["activity"] = list(self._tails.get(run["id"], []))[-60:]
        if cancelled:
            run["status"], run["error"] = "cancelled", "사용자가 취소했습니다."
        elif timed_out:
            run["status"], run["error"] = "failed", "시간 제한(%d분)을 넘겨 중단했습니다." % (self.max_runtime // 60)
        elif code not in (0, None):
            run["status"], run["error"] = "failed", "OpenCode가 오류로 끝났습니다(종료 코드 %s)." % code
        self._collect(run, keep_status=run["status"] in ("cancelled", "failed"))
        self._note_blocked(run)
        self._save(run)

    def _collect(self, run, keep_status):
        """Stage the worktree (no commit) and store the patch, file list and stats."""
        path = Path(run["worktree"])
        try:
            # Test runs and installs leave caches behind (__pycache__, node_modules ...): they are not part of the change.
            worktree._git("add", "-A", "--", ".", *[":(exclude,glob)" + pattern for pattern in NOISE_PATHS], cwd=path)
            # The patch is kept as BYTES: decoding it with the console code page (cp949 ...) and writing it back as UTF-8
            # would change every non-ASCII line, and `git apply` would then fail or corrupt Korean source files.
            patch = _git_bytes("diff", "--cached", "--binary", "HEAD", cwd=path)
            numstat = _git_bytes("diff", "--cached", "--numstat", "HEAD", cwd=path).decode("utf-8", "replace")
            names = _git_bytes("diff", "--cached", "--name-status", "HEAD", cwd=path).decode("utf-8", "replace")
            head = worktree._git("rev-parse", "HEAD", cwd=path).strip()
        except RuntimeError as error:
            if not keep_status:
                run["status"], run["error"] = "failed", "변경 내용을 읽지 못했습니다: " + str(error)[:160]
            return
        (self._path(run["id"]) / "patch.diff").write_bytes(patch)
        status = {}
        for line in names.splitlines():
            code, _, file = line.partition("\t")
            status[file] = code[:1]
        files = []
        for line in numstat.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            added, deleted, file = parts[0], parts[1], parts[-1]
            files.append({"path": file, "status": status.get(file, "M"), "added": int(added) if added.isdigit() else 0,
                          "deleted": int(deleted) if deleted.isdigit() else 0, "binary": added == "-"})
        run["files"] = files
        run["stat"] = "%d개 파일, +%d −%d" % (len(files), sum(f["added"] for f in files), sum(f["deleted"] for f in files))
        if head != run["base"]:
            run["error"] = (run["error"] + " " if run["error"] else "") + "작업 중 에이전트가 커밋을 만들었습니다(이 변경은 그대로 패치에 포함되지 않을 수 있음)."
        if not keep_status:
            run["status"] = "review" if files else "nochange"

    # -- actions ------------------------------------------------------------------------------------------------
    def cancel(self, run_id):
        run = self._read(run_id)
        if run["status"] != "running":
            raise CodeWorkError("실행 중인 작업이 아닙니다.")
        self._cancel_flags[run_id] = True
        proc = self._procs.get(run_id)
        if proc is not None:
            kill_tree(proc)

    def apply(self, run_id, confirm):
        """Copy the patch into the user's folder (uncommitted). Needs an explicit confirm and a clean `git apply --check`."""
        if confirm is not True:
            raise CodeWorkError("변경을 내 폴더에 적용하려면 확인이 필요합니다.")
        run = self._read(run_id)
        if run["status"] != "review":
            raise CodeWorkError("적용할 변경이 없습니다(검토 대기 상태가 아님).")
        patch = self._path(run_id) / "patch.diff"
        repo = self.check_repo(run["repo"])
        check = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn", str(patch)], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if check.returncode != 0:
            raise CodeWorkError("내 폴더에 바로 적용할 수 없습니다(같은 파일을 이미 고쳤거나 내용이 달라졌습니다): " + (check.stderr.strip() or check.stdout.strip())[-240:])
        done = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            raise CodeWorkError("적용하지 못했습니다: " + (done.stderr.strip() or done.stdout.strip())[-240:])
        run["status"], run["applied_at"] = "applied", self.clock()
        self._save(run)
        self._remove_worktree(run)

    # -- command approval ---------------------------------------------------------------------------------------
    def _allowed_patterns(self, run):
        return [*BASH_ALLOW, *self.rules.get(run["repo"]), *run.get("once_allow", [])]

    def _note_blocked(self, run):
        """List shell commands the agent tried that the deny-by-default rule blocked, with how risky each is."""
        try:
            known = [b["command"] for b in run.get("blocked", [])]
            for command in cmdguard.find_blocked(self._path(run["id"]) / "events.jsonl", self._allowed_patterns(run), known):
                level, note = cmdguard.classify(command)
                run.setdefault("blocked", []).append({"id": len(run.get("blocked", [])) + 1, "command": command[:cmdguard.MAX_COMMAND],
                                                       "level": level, "note": note, "state": "open"})
        except OSError:
            pass

    def decide(self, run_id, item_id, decision):
        """Answer one blocked command: "once" / "always" (this project) / "decline". Allowed ones continue the same work in the same copy."""
        if decision not in ("once", "always", "decline"):
            raise CodeWorkError("허용(이번만/항상) 또는 거절 중에서 고르세요.")
        with self._lock:
            run = self._read(run_id)
            if run["status"] not in ("review", "nochange", "failed"):
                raise CodeWorkError("지금은 명령을 승인할 수 없는 상태입니다.")
            if any(t.is_alive() for t in self._threads.values()):
                raise CodeWorkError("다른 코드 작업이 실행 중입니다. 끝나거나 취소한 뒤 승인하세요.")
            item = next((b for b in run.get("blocked", []) if b["id"] == item_id and b["state"] == "open"), None)
            if item is None:
                raise CodeWorkError("처리할 수 있는 막힌 명령이 아닙니다.")
            if decision == "decline":
                item["state"] = "declined"
                run["approvals"].append({"command": item["command"], "decision": "declined", "at": self.clock()})
                self._save(run)
                return
            level, note = cmdguard.classify(item["command"])
            if level == "never":
                raise CodeWorkError("이 명령은 허용할 수 없습니다: " + note)
            if decision == "always":
                if level != "normal":
                    raise CodeWorkError("이 종류의 명령은 이번만 허용할 수 있습니다.")
                try:
                    self.rules.add(run["repo"], item["command"])
                except ValueError as error:
                    raise CodeWorkError(str(error)) from None
            else:
                run.setdefault("once_allow", []).append(item["command"])
            item["state"] = decision
            run["approvals"].append({"command": item["command"], "decision": decision, "at": self.clock()})
            binary = self.find_binary()
            if not binary:
                raise CodeWorkError("OpenCode를 찾지 못했습니다.")
            if not Path(run["worktree"]).exists():
                raise CodeWorkError("작업용 복사본이 없어 이어서 실행할 수 없습니다. 새 작업으로 다시 시작하세요.")
            model_arg, provider, _tier = self._resolve_model(run["model"], run.get("cost_ok") is True)
            config = build_config(run["model"], provider, [*self.rules.get(run["repo"]), *run.get("once_allow", [])])
            env = clean_environment(os.environ, self.opencode_home, config, key=(provider or {}).get("api_key"))
            prompt = "허용된 명령: `%s`. 이 명령을 사용해 원래 요청을 이어서 끝내세요." % item["command"]
            command = self._resume_command(binary, model_arg, prompt, run.get("session") or None)
            run["status"], run["finished"], run["error"] = "running", None, ""
            self._cancel_flags.pop(run_id, None)
            self._timed_out.discard(run_id)
            self._save(run)
            thread = threading.Thread(target=self._run, args=(run, command, env), name="code-" + run_id, daemon=True)
            self._threads[run_id] = thread
            thread.start()

    # -- second-opinion review ------------------------------------------------------------------------------------
    def pick_reviewer(self, run, model_ref="auto"):
        """-> (provider, model, ref). A local/free model that did not write the change; never OpenCode's own catalogue, never paid."""
        if model_ref in (None, "", "auto"):
            options = [o for o in self.model_options() if o["tier"] in ("local", "free") and not o["ref"].startswith("opencode/") and o["ref"] != run["model"]]
            if not options:
                raise CodeWorkError("리뷰에 쓸 다른 무료·로컬 모델이 없습니다. 서비스를 하나 더 연결하거나 모델을 직접 고르세요.")
            model_ref = max(options, key=lambda o: o["level"])["ref"]
        if not isinstance(model_ref, str) or not MODEL_REF.fullmatch(model_ref) or model_ref.startswith("opencode/"):
            raise CodeWorkError("리뷰할 모델을 고르세요(OpenCode 자체 모델은 리뷰에 쓸 수 없습니다).")
        if model_ref == run["model"]:
            raise CodeWorkError("수정한 모델과 다른 모델을 고르세요.")
        name, _, model = model_ref.partition("/")
        provider = self.providers.get(name)
        if provider is None:
            raise CodeWorkError("연결되지 않은 서비스입니다: " + name)
        if provider["data_policy"] != "local" and not provider.get("consented"):
            raise CodeWorkError("이 서비스로 코드를 보내려면 연결할 때 동의가 필요합니다.")
        if usage.tier_for(provider, model) not in ("local", "free"):
            raise CodeWorkError("리뷰에는 무료·로컬 모델만 쓸 수 있습니다.")
        return provider, model, model_ref

    def review(self, run_id, model_ref="auto"):
        with self._lock:
            run = self._read(run_id)
            patch = self._path(run_id) / "patch.diff"
            if run["status"] == "running" or not patch.exists() or not patch.stat().st_size:
                raise CodeWorkError("리뷰할 변경이 없습니다(실행 중이거나 바뀐 파일이 없음).")
            if (run.get("review") or {}).get("state") == "running":
                raise CodeWorkError("이미 리뷰 중입니다.")
            if any(t.is_alive() for t in self._threads.values()):
                raise CodeWorkError("다른 작업이 실행 중입니다. 끝난 뒤 다시 누르세요.")
            provider, model, ref = self.pick_reviewer(run, model_ref)
            run["review"] = {"state": "running", "model": ref, "started": self.clock()}
            self._save(run)
            thread = threading.Thread(target=self._run_review, args=(run, provider, model, patch), name="review-" + run_id, daemon=True)
            thread.start()

    def _run_review(self, run, provider, model, patch):
        info = run["review"]
        try:
            text = patch.read_bytes().decode("utf-8", "replace")
            info.update(self.review_fn(provider, model, run, text, run.get("checks")))
            info["state"] = "done"
        except Exception as error:                                      # network, quota, bad answer: report it, never crash
            info.update({"state": "failed", "error": codereport.mask(str(error))[:200]})
        info["finished"] = self.clock()
        self._save(run)

    # -- build/test checks ---------------------------------------------------------------------------------------
    def check(self, run_id):
        """Run the detected build/test commands in the agent's copy (only on request; one job at a time)."""
        with self._lock:
            run = self._read(run_id)
            if run["status"] in ("running", "discarded", "applied", "undone") or not Path(run.get("worktree", "")).exists():
                raise CodeWorkError("이 작업의 복사본에서는 지금 검사를 실행할 수 없습니다(실행 중이거나 이미 정리됨).")
            if any(t.is_alive() for t in self._threads.values()):
                raise CodeWorkError("다른 작업이 실행 중입니다. 끝난 뒤 다시 누르세요.")
            items = checks.plan(run["worktree"])
            run["checks"] = {"state": "running", "started": self.clock(), "items": [], "verdict": "", "planned": [i["command"] for i in items]}
            self._save(run)
            thread = threading.Thread(target=self._run_checks, args=(run, items), name="check-" + run_id, daemon=True)
            self._threads[run_id] = thread
            thread.start()

    def _run_checks(self, run, items):
        env = {k: v for k, v in os.environ.items() if k in WINDOWS_ENV_KEEP}
        env.update({"CI": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"})
        done = []
        try:
            for item in items:
                if self._cancel_flags.get(run["id"]):
                    break
                done.append(checks.run_one(item, run["worktree"], env, self.check_timeout, self.popen, kill_tree))
                run["checks"]["items"] = list(done)
                self._save(run)
        except Exception as error:                                     # the UI must always get an end state
            done.append({"kind": "?", "command": "(검사 실행)", "status": "failed", "exit": None, "seconds": 0, "output": str(error)[:200]})
        run["checks"].update({"state": "done", "items": done, "verdict": checks.verdict(done), "finished": self.clock()})
        self._save(run)

    def _resume_command(self, binary, model_arg, prompt, session):
        return self.command_fn(binary, model_arg, prompt, session=session) if session else self.command_fn(binary, model_arg, prompt)

    def undo(self, run_id, confirm):
        """Take an applied change back out of the user's folder with the reverse patch (never touches git history)."""
        if confirm is not True:
            raise CodeWorkError("적용한 변경을 되돌리려면 확인이 필요합니다.")
        run = self._read(run_id)
        if run["status"] != "applied":
            raise CodeWorkError("되돌릴 변경이 없습니다(적용된 작업이 아닙니다).")
        patch = self._path(run_id) / "patch.diff"
        if not patch.exists():
            raise CodeWorkError("되돌릴 패치 파일이 없습니다.")
        repo = self.check_repo(run["repo"])
        command = ["git", "apply", "-R", "--whitespace=nowarn"]
        check = subprocess.run(command + ["--check", str(patch)], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if check.returncode != 0:
            raise CodeWorkError("자동으로 되돌릴 수 없습니다(적용 뒤에 같은 부분을 또 고쳤습니다). 파일을 직접 확인하세요: " + (check.stderr.strip() or check.stdout.strip())[-240:])
        done = subprocess.run(command + [str(patch)], cwd=repo, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if done.returncode != 0:
            raise CodeWorkError("되돌리지 못했습니다: " + (done.stderr.strip() or done.stdout.strip())[-240:])
        run["status"], run["undone_at"] = "undone", self.clock()
        self._save(run)

    def discard(self, run_id):
        run = self._read(run_id)
        if run["status"] == "running":
            raise CodeWorkError("실행 중입니다. 먼저 취소하세요.")
        if run["status"] in ("discarded",):
            return
        self._remove_worktree(run)
        run["status"] = "discarded" if run["status"] != "applied" else "applied"
        self._save(run)

    def _remove_worktree(self, run):
        try:
            worktree.remove(run["repo"], run["worktree"])
        except (RuntimeError, ValueError, OSError):
            shutil.rmtree(run["worktree"], ignore_errors=True)
            subprocess.run(["git", "worktree", "prune"], cwd=run["repo"], capture_output=True, check=False)

    def shutdown(self):
        for proc in list(self._procs.values()):
            kill_tree(proc)
