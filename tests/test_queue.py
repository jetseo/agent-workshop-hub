import tempfile
import unittest
from pathlib import Path
from workshop.queue import Queue

class TestQueue(unittest.TestCase):
    def test_review(self):
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            task = q.add("code", "Implement feature")
            self.assertEqual(q.claim()["id"], task)
            self.assertEqual(q.finish(task, {"ok": True}), "review")
            q.approve(task)
            self.assertEqual(q.list()[0]["state"], "done")

    def test_retry_limit(self):
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            task = q.add("image", "Generate sprite")
            self.assertIsNone(q.claim())
            q.claim()
            self.assertEqual(q.finish(task, success=False), "failed")

    def test_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            q.add("qa", "Run tests")
            q.claim()
            self.assertEqual(q.recover(), 1)
            self.assertIsNone(q.claim())
