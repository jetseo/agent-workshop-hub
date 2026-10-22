import sys
import tempfile
import time
import unittest
from pathlib import Path

from workshop import checks
from workshop.codework import CodeRuns, CodeWorkError
from tests.test_codework import Base


def write(root, name, text):
    path = Path(root) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class PlanTests(unittest.TestCase):
    def test_only_detected_build_and_test_commands_are_planned(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "Makefile", "build:\n\techo\ntest:\n\techo\nlint:\n\techo\n")
            write(tmp, "pyproject.toml", "[tool.ruff]\n")
            planned = {(i["kind"], i["command"]) for i in checks.plan(tmp)}
        self.assertEqual({kind for kind, _ in planned}, {"build", "test"})                          # lint is never run
        self.assertIn(("test", "make test"), planned)

    def test_python_commands_use_the_running_interpreter(self):
        with tempfile.TemporaryDirectory() as tmp:
            write(tmp, "tests/test_a.py", "")
            items = checks.plan(tmp)
        self.assertEqual(items[0]["argv"][0], sys.executable)

    def test_verdicts(self):
        self.assertEqual(checks.verdict([]), "none")
        self.assertEqual(checks.verdict([{"status": "passed"}, {"status": "unavailable"}]), "passed")
        self.assertEqual(checks.verdict([{"status": "passed"}, {"status": "timeout"}]), "failed")
        self.assertEqual(checks.verdict([{"status": "unavailable"}]), "unavailable")
        self.assertEqual(checks.verdict([{"status": "notests"}, {"status": "unavailable"}]), "notests")


class RunChecksTests(Base):
    def setUp(self):
        super().setUp()
        write(self.repo, "tests/test_unit.py", "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n")
        self.commit_all("unit test")

    def commit_all(self, message):
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", message], cwd=self.repo, check=True)

    def wait_checks(self, run_id, timeout=60):
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = (self.runs.get(run_id).get("checks") or {}).get("state")
            if state == "done":
                return self.runs.get(run_id)["checks"]
            time.sleep(0.1)
        self.fail("checks did not finish")

    def test_passing_tests_in_the_copy_are_reported(self):
        run_id = self.wait(self.start("FIX 고쳐줘"), "review")["id"]
        self.runs.check(run_id)
        result = self.wait_checks(run_id)
        self.assertEqual(result["verdict"], "passed", result)
        self.assertEqual(result["items"][0]["status"], "passed")
        self.assertIn("## 빌드·테스트·리뷰", (self.runs._path(run_id) / "report.md").read_text(encoding="utf-8"))
        self.assertIn("**통과**", (self.runs._path(run_id) / "report.md").read_text(encoding="utf-8"))
        self.assertEqual(self.runs.get(run_id)["status"], "review")                                 # checking never changes the verdict to apply

    def test_failing_tests_are_reported_with_output(self):
        run_id = self.wait(self.start("NEWFILE"), "review")["id"]                                   # the bug (a - b) is still there
        self.runs.check(run_id)
        result = self.wait_checks(run_id)
        self.assertEqual(result["verdict"], "failed")
        self.assertEqual(result["items"][0]["status"], "failed")
        self.assertTrue(result["items"][0]["output"])

    def test_a_slow_command_is_stopped_at_the_time_limit(self):
        self.runs.check_timeout = 1
        write(self.repo, "tests/test_slow.py", "import time, unittest\nclass T(unittest.TestCase):\n    def test_slow(self):\n        time.sleep(30)\n")
        self.commit_all("slow")
        run_id = self.wait(self.start("NEWFILE"), "review")["id"]
        self.runs.check(run_id)
        result = self.wait_checks(run_id)
        self.assertEqual(result["items"][0]["status"], "timeout")
        self.assertEqual(result["verdict"], "failed")

    def test_a_run_that_finds_no_tests_is_not_a_pass(self):
        import subprocess
        subprocess.run(["git", "rm", "-q", "-r", "tests"], cwd=self.repo, check=True)
        write(self.repo, "empty/test_none.py", "x = 1\n")                                    # looks like a test file, holds no tests
        self.commit_all("no tests")
        run_id = self.wait(self.start("NEWFILE"), "review")["id"]
        self.runs.check(run_id)
        result = self.wait_checks(run_id)
        self.assertEqual(result["items"][0]["status"], "notests")
        self.assertEqual(result["verdict"], "notests")

    def test_checks_cannot_start_for_running_or_finished_runs(self):
        running = self.start("SLEEP")
        with self.assertRaises(CodeWorkError):
            self.runs.check(running)
        self.runs.cancel(running)
        self.wait(running)
        applied = self.wait(self.start("FIX"), "review")["id"]
        self.runs.apply(applied, True)
        with self.assertRaises(CodeWorkError):
            self.runs.check(applied)


if __name__ == "__main__":
    unittest.main()
