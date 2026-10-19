import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from workshop.board import Board
from workshop import adapters


class BoardTests(unittest.TestCase):
    def make(self, d):
        return Board.create(d, "Task", "Write a plan", "No network")

    def test_serial_dependency_gating(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.make(d)
            b.add_step("plan", "ollama/a", prompt="plan it")
            b.add_step("review", "ollama/b", depends_on=["plan"], prompt="review it")
            self.assertEqual(b.ready_steps(), ["plan"])
            b.write_step_output("plan", "THE PLAN")
            b.merge_step("plan")
            self.assertEqual(b.ready_steps(), ["review"])
            ctx = b.context_for("review")
            self.assertIn("THE PLAN", ctx)
            self.assertIn("review it", ctx)
            self.assertIn("THE PLAN", (b.folder / "board.md").read_text(encoding="utf-8"))

    def test_parallel_steps_write_separate_files(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.make(d)
            for n in ("a", "b", "c"):
                b.add_step(n, "m/" + n)
            self.assertEqual(sorted(b.ready_steps()), ["a", "b", "c"])
            threads = [threading.Thread(target=b.write_step_output, args=(n, "out-" + n))
                       for n in ("a", "b", "c")]
            [t.start() for t in threads]; [t.join() for t in threads]
            for n in ("a", "b", "c"):
                b.merge_step(n)
            text = (b.folder / "board.md").read_text(encoding="utf-8")
            for n in ("a", "b", "c"):
                self.assertIn("out-" + n, text)

    def test_invalid_input_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.make(d)
            for bad in ("../x", "A B", "", "x" * 80):
                with self.assertRaises(ValueError):
                    b.add_step(bad, "m/x")
            b.add_step("one", "m/x")
            with self.assertRaises(ValueError):
                b.add_step("one", "m/x")
            with self.assertRaises(ValueError):
                b.add_step("two", "m/x", depends_on=["missing"])
            with self.assertRaises(ValueError):
                b.merge_step("one")  # no output yet

    def test_history_and_truncation(self):
        with tempfile.TemporaryDirectory() as d:
            b = self.make(d)
            b.add_context("decided X")
            b.add_context("decided Y")
            self.assertGreaterEqual(len(list((b.folder / ".history").glob("*.md"))), 2)
            b.add_step("s", "m/x", prompt="DO IT")
            b.add_context("z" * 50000)
            ctx = b.context_for("s", max_chars=1000)
            self.assertLessEqual(len(ctx), 1100)
            self.assertTrue(ctx.rstrip().endswith("DO IT"))


class OpenCodeModelTests(unittest.TestCase):
    def run_with(self, model):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            done = MagicMock(returncode=0, stdout="", stderr="")
            with patch("workshop.adapters.shutil.which", return_value="/bin/opencode"), \
                 patch("workshop.adapters.subprocess.run", return_value=done) as run:
                adapters.run_opencode("hi", d, enabled=True, model=model)
                return run.call_args[0][0]

    def test_model_flag_passed_as_argument(self):
        self.assertEqual(self.run_with("ollama/qwen2.5-coder:7b"),
                         ["/bin/opencode", "run", "--model", "ollama/qwen2.5-coder:7b", "hi"])
        self.assertEqual(self.run_with(None), ["/bin/opencode", "run", "hi"])

    def test_stdin_is_never_inherited(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            done = MagicMock(returncode=0, stdout="", stderr="")
            with patch("workshop.adapters.shutil.which", return_value="/bin/opencode"), \
                 patch("workshop.adapters.subprocess.run", return_value=done) as run:
                adapters.run_opencode("hi", d, enabled=True)
                self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_bad_model_rejected(self):
        for bad in ("--evil", "no-slash", "a/b; rm -rf", "a/ b", "x" * 300):
            with self.assertRaises(ValueError):
                self.run_with(bad)


if __name__ == "__main__":
    unittest.main()


class WindowsInteropTests(unittest.TestCase):
    def test_windows_opencode_is_refused_under_wsl(self):
        import os
        win = "/mnt/c/Users/x/AppData/Roaming/npm/node_modules/opencode-ai/bin/opencode.exe"
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / ".git").mkdir()
            with patch("workshop.adapters.shutil.which", return_value=win), \
                 patch.dict(os.environ, {}, clear=False):
                os.environ.pop("WORKSHOP_ALLOW_WINDOWS_OPENCODE", None)
                self.assertIsNone(adapters.find_opencode())
                with self.assertRaises(FileNotFoundError) as ctx:
                    adapters.run_opencode("hi", d, enabled=True)
                self.assertIn("install-opencode.sh", str(ctx.exception))
            done = MagicMock(returncode=0, stdout="", stderr="")
            with patch("workshop.adapters.shutil.which", return_value=win), \
                 patch.dict(os.environ, {"WORKSHOP_ALLOW_WINDOWS_OPENCODE": "1"}), \
                 patch("workshop.adapters.subprocess.run", return_value=done):
                self.assertEqual(adapters.find_opencode(), win)
