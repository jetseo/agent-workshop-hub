"""Regression tests for executor errors and queue state."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.queue import Queue
from workshop.worker import process_one


class WorkerFailureTests(unittest.TestCase):
    def test_opencode_error_is_persisted(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.sqlite3")
            job_id = queue.add("code", "Implement", {"workspace": directory})
            with patch("workshop.worker.run_opencode", side_effect=RuntimeError("agent unavailable")):
                result = process_one(queue, allow_opencode=True)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(result["job_id"], job_id)
            self.assertIn("agent unavailable", result["result"]["error"])
            self.assertEqual(queue.list()[0]["state"], "failed")
            self.assertIsNone(queue.claim(kinds=["code"]))

    def test_nonzero_exit_is_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.sqlite3")
            queue.add("code", "Implement", {"workspace": directory})
            with patch("workshop.worker.run_opencode", return_value={"exit_code": 1, "stderr": "bad"}):
                result = process_one(queue, allow_opencode=True)
            self.assertEqual(result["state"], "failed")
            self.assertEqual(queue.list()[0]["state"], "failed")

    def test_approval_blocks_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.sqlite3")
            queue.add("code", "Implement", {"workspace": directory}, require_approval=True)
            with patch("workshop.worker.run_opencode") as agent:
                self.assertIsNone(process_one(queue, allow_opencode=True))
                agent.assert_not_called()
            self.assertEqual(queue.list()[0]["state"], "pending_approval")
