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

    def test_image_outputs_saved_for_review(self):
        self.queue.add("image", "Render", {"workflow": "workflow.json",
                                           "save_outputs": True,
                                           "output_dir": self.temp.name})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "abc"}), \
             patch("workshop.worker.wait_for_images", return_value=[{"filename": "test.png"}]), \
             patch("workshop.worker.download_images", return_value=[{"path": "test.png"}]), \
             patch("workshop.worker.save_report", return_value=Path(self.temp.name) / "report.json"):
            result = process_one(self.queue, allow_comfyui=True)
        self.assertEqual(result["state"], "review")
        self.assertEqual(result["result"]["files"][0]["path"], "test.png")

    def test_image_output_requires_destination(self):
        self.queue.add("image", "Render", {"workflow": "workflow.json",
                                           "save_outputs": True})
        with patch("workshop.worker.submit_workflow") as submit:
            result = process_one(self.queue, allow_comfyui=True)
            submit.assert_not_called()
        self.assertEqual(result["state"], "failed")

    def test_image_output_rejects_relative_destination_before_submit(self):
        self.queue.add("image", "Render", {"workflow": "workflow.json",
                                           "save_outputs": True,
                                           "output_dir": "relative/images"})
        with patch("workshop.worker.submit_workflow") as submit:
            result = process_one(self.queue, allow_comfyui=True)
            submit.assert_not_called()
        self.assertEqual(result["state"], "failed")
    def test_rejects_unsafe_comfyui_prompt_identifier(self):
        self.queue.add('image', 'Render', {'workflow': 'workflow.json',
                                           'save_outputs': True,
                                           'output_dir': self.temp.name})
        with patch('workshop.worker.submit_workflow', return_value={'prompt_id': '../outside'}), \
             patch('workshop.worker.wait_for_images', return_value=[]), \
             patch('workshop.worker.download_images', return_value=[]), \
             patch('workshop.worker.save_report') as save:
            result = process_one(self.queue, allow_comfyui=True)
            save.assert_not_called()
        self.assertEqual(result['state'], 'failed')
    def test_invalid_prompt_id_stops_before_network_polling(self):
        self.queue.add("image", "Render", {"workflow": "workflow.json",
                                           "save_outputs": True,
                                           "output_dir": self.temp.name})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": "../escape"}), \
             patch("workshop.worker.wait_for_images") as poll, \
             patch("workshop.worker.download_images") as download:
            result = process_one(self.queue, allow_comfyui=True)
            poll.assert_not_called()
            download.assert_not_called()
        self.assertEqual(result["state"], "failed")

    def test_invalid_prompt_id_without_save_outputs(self):
        self.queue.add("image", "Render", {"workflow": "workflow.json"})
        with patch("workshop.worker.submit_workflow", return_value={"prompt_id": None}), \
             patch("workshop.worker.wait_for_completion") as poll:
            result = process_one(self.queue, allow_comfyui=True)
            poll.assert_not_called()
        self.assertEqual(result["state"], "failed")

    def test_invalid_kind_not_executed(self):
        self.queue.add("image", "Generate image")
        with patch("workshop.worker.run_opencode") as runner:
            result = process_one(self.queue, allow_opencode=True)
            runner.assert_not_called()
        self.assertIsNone(result)
        self.assertEqual(self.queue.list()[0]["state"], "queued")

if __name__ == "__main__":
    unittest.main()


class WorkerInterruptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def test_interrupt_fails_job_for_review_and_is_not_replayed(self):
        task = self.queue.add("code", "edit files", {"workspace": self.temp.name})
        with patch("workshop.worker.run_opencode", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                process_one(self.queue, allow_opencode=True)
        job = self.queue.list()[0]
        self.assertEqual(job["state"], "failed")
        self.assertIn("Interrupted", job["result"])
        self.assertIsNone(self.queue.claim())  # side effects unknown: never auto-retried

    def test_failed_execution_is_not_retried_automatically(self):
        self.queue.add("code", "edit files", {"workspace": self.temp.name})
        with patch("workshop.worker.run_opencode", return_value={"exit_code": 1}) as run:
            self.assertEqual(process_one(self.queue, allow_opencode=True)["state"], "failed")
            self.assertIsNone(process_one(self.queue, allow_opencode=True))
        self.assertEqual(run.call_count, 1)

    def test_recovered_while_running_reports_failure_without_crashing(self):
        self.queue.add("code", "slow", {"workspace": self.temp.name})

        def recovered_meanwhile(*_a, **_k):
            self.queue.recover()  # another operator marked it stale mid-run
            return {"exit_code": 0}

        with patch("workshop.worker.run_opencode", side_effect=recovered_meanwhile):
            outcome = process_one(self.queue, allow_opencode=True)
        self.assertEqual(outcome["state"], "failed")
        self.assertIn("storage_error", outcome["result"])
        self.assertEqual(self.queue.list()[0]["state"], "failed")

    def test_disabled_executor_leaves_other_kinds_untouched(self):
        image = self.queue.add("image", "img", {"workflow": "{}"})
        self.assertIsNone(process_one(self.queue, allow_opencode=True))
        job = self.queue.list()[0]
        self.assertEqual((job["id"], job["state"], job["attempts"]), (image, "queued", 0))
