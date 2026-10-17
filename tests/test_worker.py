import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.queue import Queue
from workshop.worker import process_one

class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_opt_in_required(self):
        self.queue.add("code", "Test", {"workspace": self.temp.name})
        with self.assertRaises(PermissionError):
            process_one(self.queue)
        self.assertEqual(self.queue.list()[0]["state"], "queued")

    def test_success_requires_review(self):
        self.queue.add("code", "Implement", {"workspace": self.temp.name})
        with patch("workshop.worker.run_opencode", return_value={"exit_code": 0, "stdout": "ok"}):
            result = process_one(self.queue, allow_opencode=True)
        self.assertEqual(result["state"], "review")

    def test_invalid_kind_not_executed(self):
        self.queue.add("image", "Generate image")
        with patch("workshop.worker.run_opencode") as runner:
            result = process_one(self.queue, allow_opencode=True)
            runner.assert_not_called()
        self.assertIn("error", result)

if __name__ == "__main__":
    unittest.main()
