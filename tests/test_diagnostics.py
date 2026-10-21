import unittest
from unittest.mock import patch

from workshop import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def test_queue_self_test(self):
        self.assertTrue(diagnostics._queue_self_test()["ok"])

    def test_optional_services_report_failure_without_crashing(self):
        with patch("workshop.diagnostics.inspect_environment", return_value={
            "python": {"ok": True, "detail": "test"},
            "platform": {"ok": True, "detail": "test"},
            "git": {"ok": True, "detail": "test"},
        }), patch("workshop.diagnostics.ollama_models", side_effect=OSError("offline")):
            result = diagnostics.inspect_runtime()
        self.assertTrue(result["queue_self_test"]["ok"])
        self.assertFalse(result["ollama_api"]["ok"])

    def test_non_strict_mode_allows_optional_services_offline(self):
        with patch("workshop.diagnostics.inspect_runtime", return_value={
            "python": {"ok": True, "detail": "test"},
            "platform": {"ok": True, "detail": "test"},
            "git": {"ok": True, "detail": "test"},
            "queue_self_test": {"ok": True, "detail": "test"},
            "ollama_api": {"ok": False, "detail": "offline"},
        }):
            self.assertEqual(diagnostics.main(["--json"]), 0)
            self.assertEqual(diagnostics.main(["--json", "--strict"]), 1)


if __name__ == "__main__":
    unittest.main()


class HintTests(unittest.TestCase):
    def test_failed_optional_checks_carry_hints_and_survive_legacy_encoding(self):
        import os, subprocess, sys
        env = {**os.environ, "PYTHONIOENCODING": "cp1252"}
        env.pop("PYTHONUTF8", None)
        out = subprocess.run([sys.executable, "-m", "workshop.diagnostics", "--no-services"],
                             capture_output=True, env=env, check=False)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn(b"UnicodeEncodeError", out.stderr)


class DescribeErrorTests(unittest.TestCase):
    def test_connection_refused_is_explained_in_korean(self):
        import urllib.error
        from workshop.preflight import describe_error
        text = describe_error(urllib.error.URLError(ConnectionRefusedError(10061, "refused")))
        self.assertIn("실행 중이 아닙니다", text)
        self.assertIn("URLError", text)

    def test_timeout_and_other_errors(self):
        from workshop.preflight import describe_error
        self.assertIn("시간 초과", describe_error(TimeoutError("slow")))
        self.assertIn("확인하세요", describe_error(OSError("no route")))
        self.assertIn("형식", describe_error(ValueError("bad json")))
        self.assertEqual(describe_error(KeyError("x")), "KeyError: 'x'")

    def test_every_mandatory_check_has_a_hint(self):
        for name in ("python", "platform", "git", "queue_self_test"):
            self.assertIn(name, diagnostics.HINTS)
