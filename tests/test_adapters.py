import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.adapters import run_opencode, ollama_models

class AdapterTests(unittest.TestCase):
    def test_opencode_disabled_by_default(self):
        with self.assertRaises(PermissionError):
            run_opencode("test", "/tmp")

    def test_no_binary(self):
        with patch("workshop.adapters.shutil.which", return_value=None):
            with self.assertRaises(FileNotFoundError):
                run_opencode("test", "/tmp", enabled=True)

    def test_requires_git_workspace(self):
        with tempfile.TemporaryDirectory() as d:
            with patch("workshop.adapters.shutil.which", return_value="/usr/bin/opencode"):
                with self.assertRaises(ValueError):
                    run_opencode("test", d, enabled=True)

    def test_reject_remote_health_check(self):
        with self.assertRaises(ValueError):
            ollama_models("http://example.com:11434")

    def test_command_arguments_not_shell(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, ".git").mkdir()
            with patch("workshop.adapters.shutil.which", return_value="/usr/bin/opencode"), patch("workshop.adapters.subprocess.run") as run:
                run.return_value.returncode = 0
                run.return_value.stdout = "ok"
                run.return_value.stderr = ""
                run_opencode("hello; echo unsafe", d, enabled=True)
                args = run.call_args.args[0]
                self.assertEqual(args, ["/usr/bin/opencode", "run", "hello; echo unsafe"])
                self.assertFalse(run.call_args.kwargs.get("shell", False))
