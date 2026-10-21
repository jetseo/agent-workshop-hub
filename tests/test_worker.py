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

    def test_kind_without_an_executor_is_not_executed(self):
        self.queue.add("plan", "Write a plan")
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
        plan = self.queue.add("plan", "p")
        self.assertIsNone(process_one(self.queue, allow_opencode=True))
        job = self.queue.list()[0]
        self.assertEqual((job["id"], job["state"], job["attempts"]), (plan, "queued", 0))
