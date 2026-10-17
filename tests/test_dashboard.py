import tempfile
import unittest
from pathlib import Path
from workshop.dashboard import make_handler
from workshop.queue import Queue

class DashboardTests(unittest.TestCase):
    def test_handler_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            handler = make_handler(queue, "test-token")
            self.assertTrue(callable(handler.do_GET))
            self.assertTrue(callable(handler.do_POST))

    def test_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            job_id = queue.add("code", "Write tests")
            with self.assertRaises(ValueError):
                queue.approve(job_id)
