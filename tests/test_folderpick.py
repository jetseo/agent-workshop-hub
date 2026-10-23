import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import folderpick


class FolderPickTests(unittest.TestCase):
    def fake(self, out=b"", error=None):
        def run(args, **kw):
            self.assertIn("-STA", args)
            if error:
                raise error
            return subprocess.CompletedProcess(args, 0, out, b"")
        return run

    def test_returns_the_chosen_path(self):
        self.assertEqual(folderpick.pick(self.fake("D:\work\내 앱\r\n".encode("utf-8")), platform="win32"), "D:\work\내 앱")

    def test_cancel_gives_an_empty_path(self):
        self.assertEqual(folderpick.pick(self.fake(b""), platform="win32"), "")

    def test_failures_and_other_platforms_give_an_empty_path(self):
        self.assertEqual(folderpick.pick(self.fake(error=OSError("no powershell")), platform="win32"), "")
        self.assertEqual(folderpick.pick(self.fake(error=subprocess.TimeoutExpired("x", 1)), platform="win32"), "")
        self.assertEqual(folderpick.pick(self.fake(b"x"), platform="linux"), "")


if __name__ == "__main__":
    unittest.main()
