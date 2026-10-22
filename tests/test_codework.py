import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from workshop import adapters, codework
from workshop.codework import CodeRuns, CodeWorkError
from workshop.providers import Providers

FAKE = r'''
import json, os, sys, time
prompt = sys.argv[-1]
model = sys.argv[sys.argv.index("-m") + 1]
def emit(kind, **part):
    print(json.dumps({"type": kind, "part": part}), flush=True)
emit("step_start")
if "SLEEP" in prompt:
    emit("text", text="working slowly")
    time.sleep(60)
if "ENV" in prompt:
    open("env.txt", "w").write("\n".join(sorted(os.environ)))
    open("cfg.json", "w").write(os.environ.get("OPENCODE_CONFIG_CONTENT", ""))
    open("argv.txt", "w", encoding="utf-8").write(" | ".join(sys.argv[1:]))
if "FIX" in prompt:
    src = open("calc.py", "rb").read().replace(b"a - b", b"a + b")       # bytes: the file may hold Korean text and a BOM
    open("calc.py", "wb").write(src)
    emit("tool_use", tool="edit", state={"status": "completed", "input": {"filePath": "calc.py"}})
if "NOISE" in prompt:
    os.makedirs("__pycache__", exist_ok=True); open("__pycache__/calc.cpython-312.pyc", "wb").write(b"\x00\x01")
    os.makedirs("sub/.pytest_cache", exist_ok=True); open("sub/.pytest_cache/v", "w").write("x")
    os.makedirs("node_modules/pkg", exist_ok=True); open("node_modules/pkg/index.js", "w").write("x")
    os.makedirs("src", exist_ok=True); open("src/pycache_notes.txt", "w").write("a real file\n")
if "NEWFILE" in prompt:
    open("notes.txt", "w").write("hello\n")
if "BAD" in prompt:
    emit("tool_use", tool="bash", state={"status": "error", "input": {"command": "rm -rf /"}, "error": "denied by rule"})
emit("text", text="done " + model)
sys.exit(3 if "FAIL" in prompt else 0)
'''


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout


def make_repo(root):
    repo = Path(root) / "proj"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text("from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return repo


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.repo = make_repo(self.dir)
        script = self.dir / "fake_opencode.py"
        script.write_text(FAKE, encoding="utf-8")
        self.providers = Providers(self.dir / "home" / "providers.json")
        self.providers.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local", models=["qwen2.5-coder:32b", "tiny-3b"])
        self.providers.add("cloud", base_url="https://api.example.com/v1", data_policy="cloud-may-train", free_all=True, models=["big-pro-model"])
        self.providers.set_consent("cloud")
        self.providers.set_key("cloud", "sk-cloud-secret-123456")
        self.providers.add("pay", base_url="https://pay.example.com/v1", data_policy="cloud-may-train", models=["gpt-x"])
        self.providers.set_consent("pay")
        self.providers.add("nope", base_url="https://nope.example.com/v1", data_policy="cloud-may-train", models=["m-free"])
        self.runs = CodeRuns(self.providers, find_binary=lambda: "fake-opencode", worktree_base=self.dir / "wt",
                             command_fn=lambda binary, model, prompt: [sys.executable, str(script), "run", "-m", model, prompt])
        self.addCleanup(self.runs.shutdown)

    def start(self, prompt, model="loc/qwen2.5-coder:32b", **kw):
        run = self.runs.start(str(self.repo), prompt, model, **kw)
        return run["id"]

    def wait(self, run_id, status=None, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            run = self.runs.get(run_id)
            if run["status"] != "running" and (status is None or run["status"] == status):
                return run
            if run["status"] != "running":                                                   # ended differently than expected: say how
                self.fail("run ended as %s, expected %s: %s" % (run["status"], status, run["error"]))
            time.sleep(0.1)
        self.fail("run did not finish: " + json.dumps(self.runs.get(run_id), ensure_ascii=False)[:300])


class RepoCheckTests(Base):
    def test_unsafe_folders_are_refused(self):
        for bad in (self.dir / "missing", self.dir, Path(self.dir.anchor), Path.home(), Path.home() / ".agent-workshop" / "x"):
            with self.assertRaises(CodeWorkError, msg=str(bad)):
                CodeRuns.check_repo(str(bad))
        self.assertEqual(CodeRuns.check_repo(str(self.repo)), self.repo.resolve())


class RunTests(Base):
    def test_a_run_changes_only_the_worktree_and_ends_in_review_with_a_diff(self):
        run_id = self.start("FIX the add function")
        run = self.wait(run_id, "review")
        self.assertEqual([f["path"] for f in run["files"]], ["calc.py"])
        self.assertEqual((run["files"][0]["status"], run["files"][0]["added"], run["files"][0]["deleted"]), ("M", 1, 1))
        self.assertIn("+    return a + b", run["patch"])
        self.assertIn("1개 파일", run["stat"])
        self.assertTrue(any("edit" in line for line in run["activity"]))
        self.assertIn("a - b", (self.repo / "calc.py").read_text(encoding="utf-8"))        # the user's folder is untouched
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "")
        self.assertEqual(run["tier"], "local")
        self.assertTrue(Path(self.runs._read(run_id)["worktree"]).is_dir())

    def test_project_summary_is_read_stored_and_sent_with_the_request(self):
        run_id = self.start("고쳐줘")
        run = self.wait(run_id)
        self.assertIn("프로젝트 요약", run["project"])
        self.assertIn("calc.py", run["project"])
        self.assertEqual(run["prompt"], "고쳐줘")

    def test_new_files_are_part_of_the_patch_and_no_change_is_reported(self):
        run = self.wait(self.start("NEWFILE please"), "review")
        self.assertEqual([(f["path"], f["status"]) for f in run["files"]], [("notes.txt", "A")])
        self.assertEqual(self.wait(self.start("just look around"), "nochange")["files"], [])

    def test_failure_exit_code_is_reported_and_partial_changes_are_kept_for_review(self):
        run = self.wait(self.start("FIX and then FAIL"), "failed")
        self.assertIn("종료 코드 3", run["error"])
        self.assertEqual([f["path"] for f in run["files"]], ["calc.py"])

    def test_the_agent_gets_a_minimal_environment_and_never_the_users_tokens(self):
        os.environ["GITHUB_TOKEN"] = "ghp_should_not_leak"
        os.environ["OPENAI_API_KEY"] = "sk-should-not-leak"
        self.addCleanup(os.environ.pop, "GITHUB_TOKEN", None)
        self.addCleanup(os.environ.pop, "OPENAI_API_KEY", None)
        run_id = self.start("ENV check", model="cloud/big-pro-model")
        self.wait(run_id)
        wt = Path(self.runs._read(run_id)["worktree"])
        names = (wt / "env.txt").read_text(encoding="utf-8").split("\n")
        self.assertNotIn("GITHUB_TOKEN", names)
        self.assertNotIn("OPENAI_API_KEY", names)
        for needed in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "OPENCODE_CONFIG_CONTENT", "JETAI_PROVIDER_KEY"):
            self.assertIn(needed, names)
        config = (wt / "cfg.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-cloud-secret-123456", config)                                  # the key travels in one variable, not in the config
        cfg = json.loads(config)
        self.assertEqual(cfg["permission"]["bash"]["*"], "deny")
        self.assertEqual((cfg["permission"]["webfetch"], cfg["permission"]["external_directory"]), ("deny", "deny"))
        self.assertEqual(cfg["provider"]["jetai"]["options"]["baseURL"], "https://api.example.com/v1")
        self.assertEqual(cfg["provider"]["jetai"]["options"]["apiKey"], "{env:JETAI_PROVIDER_KEY}")
        self.assertTrue(cfg["provider"]["jetai"]["models"]["big-pro-model"]["tool_call"])
        self.assertIn("jetai/big-pro-model", (wt / "argv.txt").read_text(encoding="utf-8"))
        isolated = Path(self.providers.path.parent) / "opencode"
        self.assertTrue((isolated / "data").is_dir() and (isolated / "config").is_dir())

    def test_the_prompt_is_one_argument_never_a_shell_string(self):
        evil = "FIX it; echo hacked & del *.* | whoami \"quoted\" 'single' $(x)"
        run = self.wait(self.start(evil), "review")
        self.assertEqual(self.runs._read(run["id"])["prompt"], evil)
        self.assertFalse((self.repo / "hacked").exists())


class ModelRuleTests(Base):
    def test_free_and_local_are_allowed_paid_needs_a_confirmation(self):
        for ref in ("loc/tiny-3b", "cloud/big-pro-model"):
            self.wait(self.start("look", model=ref))
        with self.assertRaises(CodeWorkError) as caught:
            self.runs.start(str(self.repo), "look", "pay/gpt-x")
        self.assertIn("요금", str(caught.exception))
        self.wait(self.start("look", model="pay/gpt-x", confirm_cost=True))

    def test_services_without_consent_unknown_services_and_junk_are_refused(self):
        for ref in ("nope/m-free", "ghost/model", "no-slash"):
            with self.assertRaises(CodeWorkError, msg=ref):
                self.runs.start(str(self.repo), "x", ref)

    def test_opencode_models_must_be_free_ids(self):
        with self.assertRaises(CodeWorkError) as caught:
            self.runs.start(str(self.repo), "x", "opencode/claude-sonnet-5")
        self.assertIn("-free", str(caught.exception))
        run_id = self.start("ENV free", model="opencode/deepseek-v4.1-flash-free")
        self.wait(run_id)
        wt = Path(self.runs._read(run_id)["worktree"])
        self.assertNotIn("provider", json.loads((wt / "cfg.json").read_text(encoding="utf-8")))       # OpenCode's own catalogue, no custom provider
        self.assertNotIn("JETAI_PROVIDER_KEY", (wt / "env.txt").read_text(encoding="utf-8").split("\n"))

    def test_auto_picks_a_capable_model_and_never_a_small_local_one(self):
        self.assertIn(self.runs.auto_model("fix the bug in calc.py"), ("loc/qwen2.5-coder:32b", "cloud/big-pro-model"))
        run = self.wait(self.runs.start(str(self.repo), "look", "auto")["id"])
        self.assertNotEqual(run["model"], "loc/tiny-3b")
        small = Providers(self.dir / "small" / "providers.json")
        small.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local", models=["tiny-3b"])
        only_small = CodeRuns(small, find_binary=lambda: "x", worktree_base=self.dir / "wt2")
        with self.assertRaises(CodeWorkError) as caught:
            only_small.auto_model("anything")
        self.assertIn("중간 이상", str(caught.exception))

    def test_options_carry_honest_warnings(self):
        options = {o["ref"]: o for o in self.runs.model_options()}
        self.assertIn("작은 로컬", options["loc/tiny-3b"]["warn"])
        self.assertIn("요금", options["pay/gpt-x"]["warn"])
        self.assertEqual(options["loc/qwen2.5-coder:32b"]["warn"], "")
        self.assertNotIn("nope/m-free", options)                                           # unconsented services are not offered


class ControlTests(Base):
    def test_only_one_run_at_a_time_and_cancel_kills_it(self):
        run_id = self.start("SLEEP forever")
        deadline = time.time() + 15
        while time.time() < deadline and not self.runs._procs.get(run_id):
            time.sleep(0.05)
        proc = self.runs._procs[run_id]
        with self.assertRaises(CodeWorkError) as caught:
            self.runs.start(str(self.repo), "second", "loc/tiny-3b")
        self.assertIn("실행 중", str(caught.exception))
        self.runs.cancel(run_id)
        run = self.wait(run_id, "cancelled")
        self.assertIsNotNone(proc.poll())
        self.assertIn("취소", run["error"])
        with self.assertRaises(CodeWorkError):
            self.runs.cancel(run_id)                                                      # already over
        self.wait(self.start("another run is fine now"))

    def test_time_limit_stops_a_runaway_agent(self):
        self.runs.max_runtime = 1
        run = self.wait(self.start("SLEEP forever"), "failed")
        self.assertIn("시간 제한", run["error"])

    def test_missing_opencode_and_bad_prompts(self):
        self.runs.find_binary = lambda: None
        with self.assertRaises(CodeWorkError) as caught:
            self.runs.start(str(self.repo), "x", "loc/tiny-3b")
        self.assertIn("OpenCode", str(caught.exception))
        self.runs.find_binary = lambda: "fake"
        for bad in ("", "   ", None, "x" * (codework.MAX_PROMPT + 1)):
            with self.assertRaises(CodeWorkError):
                self.runs.start(str(self.repo), bad, "loc/tiny-3b")

    def test_a_dirty_working_folder_is_flagged(self):
        (self.repo / "scratch.txt").write_text("uncommitted", encoding="utf-8")
        run = self.wait(self.start("look"))
        self.assertTrue(run["repo_was_dirty"])


class ApplyTests(Base):
    def reviewed(self, prompt="FIX the add function"):
        return self.wait(self.start(prompt), "review")["id"]

    def test_apply_needs_confirmation_then_changes_the_folder_without_committing(self):
        run_id = self.reviewed()
        head = git(self.repo, "rev-parse", "HEAD").strip()
        with self.assertRaises(CodeWorkError):
            self.runs.apply(run_id, False)
        self.runs.apply(run_id, True)
        self.assertIn("a + b", (self.repo / "calc.py").read_text(encoding="utf-8"))
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), head)                # no commit
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "M calc.py")
        self.assertEqual(self.runs.get(run_id)["status"], "applied")
        self.assertFalse(Path(self.runs._read(run_id)["worktree"]).exists())
        with self.assertRaises(CodeWorkError):
            self.runs.apply(run_id, True)                                                  # cannot apply twice

    def test_a_conflicting_working_folder_is_not_touched(self):
        run_id = self.reviewed()
        (self.repo / "calc.py").write_text("def add(a, b):\n    return a * b\n", encoding="utf-8")        # the user changed the same line
        with self.assertRaises(CodeWorkError) as caught:
            self.runs.apply(run_id, True)
        self.assertIn("바로 적용할 수 없습니다", str(caught.exception))
        self.assertIn("a * b", (self.repo / "calc.py").read_text(encoding="utf-8"))
        self.assertEqual(self.runs.get(run_id)["status"], "review")                       # still reviewable

    def test_new_files_are_applied_too(self):
        run_id = self.wait(self.start("NEWFILE"), "review")["id"]
        self.runs.apply(run_id, True)
        self.assertEqual((self.repo / "notes.txt").read_text(encoding="utf-8").strip(), "hello")

    def test_discard_removes_the_worktree_and_branch(self):
        run_id = self.reviewed()
        record = self.runs._read(run_id)
        self.runs.discard(run_id)
        self.assertFalse(Path(record["worktree"]).exists())
        self.assertNotIn(record["branch"], git(self.repo, "branch", "--list"))
        self.assertEqual(self.runs.get(run_id)["status"], "discarded")
        self.runs.discard(run_id)                                                          # idempotent
        with self.assertRaises(CodeWorkError):
            self.runs.apply(run_id, True)

    def test_running_work_cannot_be_discarded(self):
        run_id = self.start("SLEEP")
        with self.assertRaises(CodeWorkError):
            self.runs.discard(run_id)
        self.runs.cancel(run_id)
        self.wait(run_id, "cancelled")


class ListingTests(Base):
    def test_list_get_and_id_validation(self):
        first = self.wait(self.start("FIX one"), "review")["id"]
        second = self.wait(self.start("look two"), "nochange")["id"]
        listed = [r["id"] for r in self.runs.list()]
        self.assertEqual(set(listed), {first, second})
        self.assertEqual(listed[0], second)                                                # newest first
        for bad in ("../x", "", None, "zzzzzzzzzzzz", "abc"):
            with self.assertRaises(CodeWorkError):
                self.runs.get(bad)
        self.assertTrue(self.runs.environment()["opencode"])


class NonAsciiTests(Base):
    def test_korean_sources_survive_the_whole_round_trip_byte_for_byte(self):
        original = "\ufeff# 한글 주석: 더하기\ndef add(a, b):\n    return a - b  # 빼기 버그\n".encode("utf-8")
        (self.repo / "calc.py").write_bytes(original)
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "korean")
        (self.repo / "문서.txt").write_text("처음\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "doc")
        run = self.wait(self.start("FIX and NEWFILE"), "review")
        self.assertIn("notes.txt", [f["path"] for f in run["files"]])
        self.assertIn("한글 주석", run["patch"])                                              # shown correctly, no mojibake
        self.runs.apply(run["id"], True)
        after = (self.repo / "calc.py").read_bytes()
        # BOM and Korean text are intact; git itself may normalise line endings to the PC's core.autocrlf setting
        self.assertEqual(after.replace(b"\r\n", b"\n"), original.replace(b"a - b", b"a + b"))
        self.assertEqual((self.repo / "문서.txt").read_text(encoding="utf-8"), "처음\n")

    def test_korean_file_names_are_listed_unquoted(self):
        run_dir = self.repo / "폴더"
        run_dir.mkdir()
        (run_dir / "읽어보세요.md").write_text("x\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "names")
        run = self.wait(self.start("FIX it"), "review")
        self.assertEqual([f["path"] for f in run["files"]], ["calc.py"])
        # a changed Korean path is reported readably, not as "\355\225..."
        (Path(self.runs._read(run["id"])["worktree"]) / "폴더" / "읽어보세요.md").write_text("y\n", encoding="utf-8")
        record = self.runs._read(run["id"])
        self.runs._collect(record, keep_status=True)
        self.assertIn("폴더/읽어보세요.md", [f["path"] for f in record["files"]])

class NoiseTests(Base):
    def test_caches_and_build_output_are_not_part_of_the_change(self):
        run = self.wait(self.start("FIX and NOISE"), "review")
        self.assertEqual(sorted(f["path"] for f in run["files"]), ["calc.py", "src/pycache_notes.txt"])
        self.assertNotIn("__pycache__", run["patch"])
        self.assertNotIn(".pyc", run["patch"])
        self.assertNotIn(".pytest_cache", run["patch"])
        self.assertNotIn("node_modules", run["patch"])
        self.runs.apply(run["id"], True)
        self.assertFalse((self.repo / "__pycache__").exists())
        self.assertTrue((self.repo / "src" / "pycache_notes.txt").exists())

class HelperTests(unittest.TestCase):
    def test_summaries_of_opencode_events(self):
        tool = {"type": "tool_use", "part": {"tool": "edit", "state": {"status": "completed", "input": {"filePath": "a.py"}}}}
        self.assertEqual(codework.summarise(tool), "✓ edit a.py")
        error = {"type": "tool_use", "part": {"tool": "bash", "state": {"status": "error", "input": {"command": "rm -rf /"}, "error": "denied"}}}
        self.assertIn("✗ bash rm -rf /", codework.summarise(error))
        self.assertIn("— denied", codework.summarise(error))
        self.assertTrue(codework.summarise({"type": "text", "part": {"text": "hello  world"}}).endswith("hello world"))
        self.assertIsNone(codework.summarise({"type": "step_start", "part": {}}))

    def test_environment_is_whitelisted_and_isolated(self):
        with tempfile.TemporaryDirectory() as d:
            base = {"PATH": "p", "TEMP": "t", "GITHUB_TOKEN": "x", "OPENAI_API_KEY": "y", "AWS_SECRET_ACCESS_KEY": "z", "SystemRoot": "C:\\Windows"}
            env = codework.clean_environment(base, d, "{}", key="k")
            self.assertEqual(env["JETAI_PROVIDER_KEY"], "k")
            for secret in ("GITHUB_TOKEN", "OPENAI_API_KEY", "AWS_SECRET_ACCESS_KEY"):
                self.assertNotIn(secret, env)
            self.assertTrue(env["XDG_DATA_HOME"].startswith(d))
            self.assertNotIn("JETAI_PROVIDER_KEY", codework.clean_environment(base, d, "{}"))

    def test_windows_opencode_is_found_behind_the_npm_shim(self):
        with tempfile.TemporaryDirectory() as d:
            npm = Path(d)
            exe = npm / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
            exe.parent.mkdir(parents=True)
            exe.write_bytes(b"MZ")
            shim = npm / "opencode.cmd"
            shim.write_text("@echo off", encoding="utf-8")
            self.assertEqual(adapters.windows_opencode_exe(str(shim)), str(exe))
            self.assertEqual(adapters.windows_opencode_exe(str(exe)), str(exe))
            self.assertIsNone(adapters.windows_opencode_exe(str(Path(d) / "elsewhere" / "opencode.cmd")))


if __name__ == "__main__":
    unittest.main()
