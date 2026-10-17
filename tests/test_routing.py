import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.queue import Queue
from workshop.worker import process_one

class RoutingTests(unittest.TestCase):
    def test_skips_unsupported_plan_and_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            q = Queue(Path(directory) / "tasks.db")
            q.add("plan", "Plan feature")
            q.add("qa", "Run checks")
            code_id = q.add("code", "Implement", {"workspace": directory})
            with patch("workshop.worker.run_opencode", return_value={"exit_code": 0}):
                result = process_one(q, allow_opencode=True)
            self.assertEqual(result["job_id"], code_id)
            self.assertEqual(result["state"], "review")
            self.assertEqual([job["state"] for job in q.list()], ["queued", "queued", "review"])

    def test_skips_disabled_executor_without_looping(self):
        with tempfile.TemporaryDirectory() as directory:
            q = Queue(Path(directory) / "tasks.db")
            q.add("image", "Generate", {"workflow": "/tmp/workflow.json"})
            with patch("workshop.worker.run_opencode") as runner:
                self.assertIsNone(process_one(q, allow_opencode=True))
                runner.assert_not_called()
            self.assertEqual(q.list()[0]["state"], "queued")

    def test_claim_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            q = Queue(Path(directory) / "tasks.db")
            q.add("image", "Generate")
            code_id = q.add("code", "Implement")
            self.assertEqual(q.claim(kinds=["code"])["id"], code_id)
            self.assertIsNone(q.claim(kinds=["code"]))
