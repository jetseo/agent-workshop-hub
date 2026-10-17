import unittest
from unittest.mock import patch
from workshop.comfyui_status import inspect, wait_for_completion

class StatusTests(unittest.TestCase):
    def test_pending(self):
        with patch("workshop.comfyui_status.history", return_value=None):
            self.assertEqual(inspect("abc")["state"], "pending")

    def test_completed_with_images(self):
        entry = {"status": {"completed": True, "status_str": "success"},
                 "outputs": {"4": {"images": [{"filename": "sprite.png", "subfolder": "", "type": "output"}]}}}
        with patch("workshop.comfyui_status.history", return_value=entry):
            result = inspect("abc")
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["outputs"][0]["filename"], "sprite.png")

    def test_failed(self):
        entry = {"status": {"completed": False, "status_str": "error"}, "outputs": {}}
        with patch("workshop.comfyui_status.history", return_value=entry):
            self.assertEqual(inspect("abc")["state"], "failed")

    def test_wait(self):
        with patch("workshop.comfyui_status.inspect", side_effect=[
            {"state": "pending"}, {"state": "completed", "outputs": []}]):
            with patch("workshop.comfyui_status.time.sleep"):
                self.assertEqual(wait_for_completion("abc", max_wait=5, interval=0.01)["state"], "completed")
