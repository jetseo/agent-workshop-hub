import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.queue import Queue
from workshop.usage import UsageLedger
from workshop.worker import process_one


class QueueUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(Path(self.temp.name) / "jobs.db")
        self.ledger = UsageLedger(Path(self.temp.name) / "usage.jsonl")

    def run_job(self, payload, result=None, ledger="default"):
        payload = {"workspace": self.temp.name, **payload}
        self.queue.add("code", "Fix the failing test in module x", payload)
        result = result or {"exit_code": 0, "stdout": "Done. " * 40}
        with patch("workshop.worker.run_opencode", return_value=result) as runner:
            outcome = process_one(self.queue, allow_opencode=True,
                                  ledger=self.ledger if ledger == "default" else ledger)
        return outcome, runner

    def test_free_model_run_is_booked_as_an_estimate_from_the_queue(self):
        outcome, runner = self.run_job({"model": "opencode/big-pickle-free"})
        self.assertEqual(outcome["state"], "review")
        self.assertEqual(runner.call_args.kwargs["model"], "opencode/big-pickle-free")
        summary = self.ledger.summary()
        self.assertEqual(set(summary["by_source"]), {"queue"})
        self.assertEqual(summary["by_model"][0]["model"], "opencode/big-pickle-free")
        self.assertGreater(summary["totals"]["free"]["tokens"], 0)

    def test_local_model_is_booked_as_local(self):
        self.run_job({"model": "ollama/qwen2.5-coder"})
        self.assertGreater(self.ledger.summary()["totals"]["local"]["tokens"], 0)

    def test_paid_model_is_refused_before_anything_runs(self):
        outcome, runner = self.run_job({"model": "opencode/gpt-5"})
        self.assertEqual(outcome["state"], "failed")
        self.assertIn("may cost money", outcome["result"]["error"])
        runner.assert_not_called()
        self.assertEqual(self.ledger.summary()["by_source"], {})

    def test_run_without_a_model_is_not_booked_because_the_tier_is_unknown(self):
        outcome, _ = self.run_job({})
        self.assertEqual(outcome["state"], "review")
        self.assertEqual(self.ledger.summary()["by_source"], {})

    def test_failed_run_is_not_booked(self):
        outcome, _ = self.run_job({"model": "opencode/big-pickle-free"}, result={"exit_code": 1, "stdout": "boom"})
        self.assertEqual(outcome["state"], "failed")
        self.assertEqual(self.ledger.summary()["by_source"], {})

    def test_a_broken_ledger_never_fails_the_job(self):
        class Broken:
            def record(self, **kw):
                raise OSError("disk full")
        outcome, _ = self.run_job({"model": "opencode/big-pickle-free"}, ledger=Broken())
        self.assertEqual(outcome["state"], "review")

    def test_malformed_model_value_fails_the_job_cleanly(self):
        outcome, runner = self.run_job({"model": ["x"]})
        self.assertEqual(outcome["state"], "failed")
        runner.assert_not_called()


if __name__ == "__main__":
    unittest.main()
