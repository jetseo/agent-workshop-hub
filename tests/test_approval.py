import tempfile
import unittest
from pathlib import Path
from workshop.queue import Queue

class ApprovalTests(unittest.TestCase):
    def test_pre_execution_approval(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "tasks.db")
            job_id = queue.add("code", "Modify source", require_approval=True)
            self.assertEqual(queue.list()[0]["state"], "pending_approval")
            self.assertIsNone(queue.claim(kinds=["code"]))
            with self.assertRaises(ValueError):
                queue.approve(job_id)
            queue.authorize(job_id)
            self.assertEqual(queue.claim(kinds=["code"])["id"], job_id)
            self.assertEqual(queue.finish(job_id, {"ok": True}), "review")
            queue.approve(job_id)
            self.assertEqual(queue.list()[0]["state"], "done")

    def test_no_double_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "tasks.db")
            job_id = queue.add("image", "Generate", require_approval=True)
            queue.authorize(job_id)
            with self.assertRaises(ValueError):
                queue.authorize(job_id)
