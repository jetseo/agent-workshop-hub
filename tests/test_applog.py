import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from workshop import applog

ROOT = Path(__file__).resolve().parent.parent


class AppLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_lines_are_written_and_the_file_is_private(self):
        path = Path(self.tmp.name) / "logs" / "hub.log"
        logger = applog.setup(path)
        applog.write("hello", logger)
        for handler in logger.handlers:
            handler.flush()
        self.assertIn("hello", path.read_text())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rotation_keeps_the_log_bounded(self):
        path = Path(self.tmp.name) / "hub.log"
        logger = applog.setup(path)
        for handler in logger.handlers:
            handler.maxBytes = 2000
        for i in range(400):
            applog.write("line %d %s" % (i, "x" * 40), logger)
        files = list(Path(self.tmp.name).glob("hub.log*"))
        self.assertLessEqual(len(files), applog.BACKUPS + 1)
        self.assertLess(sum(f.stat().st_size for f in files), 2000 * (applog.BACKUPS + 2))

    def test_an_unwritable_location_disables_logging_instead_of_failing(self):
        blocker = Path(self.tmp.name) / "file"
        blocker.write_text("x")
        self.assertIsNone(applog.setup(blocker / "sub" / "hub.log"))
        applog.write("still fine", None)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerLifecycleTests(unittest.TestCase):
    def start(self, port, home):
        env = {**os.environ, "HOME": home, "USERPROFILE": home, "PYTHONIOENCODING": "utf-8"}
        return subprocess.Popen([sys.executable, "-m", "workshop.dashboard", "--port", str(port), "--db", str(Path(home) / "j.db")],
                                cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")

    def test_a_busy_port_gives_a_clear_message_and_exit_code_2(self):
        with tempfile.TemporaryDirectory() as home, socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            proc = self.start(busy.getsockname()[1], home)
            try:
                _out, err = proc.communicate(timeout=20)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertEqual(proc.returncode, 2)
            self.assertIn("이미", err)
            self.assertNotIn("Traceback", err)

    @unittest.skipIf(os.name == "nt", "SIGTERM semantics differ on Windows")
    def test_sigterm_stops_the_server_cleanly_and_is_logged(self):
        with tempfile.TemporaryDirectory() as home:
            proc = self.start(_free_port(), home)
            try:
                for line in proc.stdout:
                    if "Loopback only" in line:
                        break
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=20)
            finally:
                if proc.poll() is None:
                    proc.kill()
            self.assertEqual(proc.returncode, 0)
            log = (Path(home) / ".agent-workshop" / "logs" / "hub.log").read_text()
            self.assertIn("start port=", log)
            self.assertIn("stop", log)


if __name__ == "__main__":
    unittest.main()
