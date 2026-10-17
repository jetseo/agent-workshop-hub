import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from workshop.queue import Queue
from workshop.worker import process_one

class ImageWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.tmp.name) / "jobs.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_completed_image_enters_review(self):
        self.queue.add("image", "Sprite", {"workflow": "/tmp/workflow.json"})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "p1"}), patch(
                "workshop.worker.wait_for_completion", return_value={"state": "completed", "outputs": []}):
            result = process_one(self.queue, allow_comfyui=True)
        self.assertEqual(result["state"], "review")

    def test_timeout_fails_without_retry(self):
        self.queue.add("image", "Sprite", {"workflow": "/tmp/workflow.json"})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "p1"}), patch(
                "workshop.worker.wait_for_completion", return_value={"state": "timeout", "outputs": []}):
            result = process_one(self.queue, allow_comfyui=True)
        self.assertEqual(result["state"], "failed")
        self.assertIsNone(self.queue.claim())
