import tempfile
import unittest
from pathlib import Path
from workshop.dashboard import make_handler, gallery_files, gallery_signature
from workshop.queue import Queue

class DashboardTests(unittest.TestCase):
    def test_handler_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            handler = make_handler(queue, "test-token")
            self.assertTrue(callable(handler.do_GET))
            self.assertTrue(callable(handler.do_POST))

    def test_gallery_only_for_reviewed_image_results(self):
        import json
        item = {"path": "/tmp/render.png", "sha256": "a" * 64}
        job = {"kind": "image", "state": "review", "result": json.dumps({"files": [item]})}
        self.assertEqual(len(gallery_files(job)), 1)
        job["state"] = "queued"
        self.assertEqual(gallery_files(job), [])
        job["state"] = "review"
        job["kind"] = "code"
        self.assertEqual(gallery_files(job), [])

    def test_gallery_signature_scoped_to_job_and_index(self):
        self.assertNotEqual(gallery_signature("token", "a", 0),
                            gallery_signature("token", "a", 1))
        self.assertNotEqual(gallery_signature("token", "a", 0),
                            gallery_signature("token", "b", 0))

    def test_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            job_id = queue.add("code", "Write tests")
            with self.assertRaises(ValueError):
                queue.approve(job_id)
