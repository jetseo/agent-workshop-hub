import tempfile
import threading
import unittest
from pathlib import Path

from workshop.queue import Queue
from workshop.router import estimate_difficulty, route


class RouterTests(unittest.TestCase):
    MODELS = ["ollama/qwen2.5-coder:7b", "ollama/qwen2.5-coder:32b", "opencode/big-pickle-free",
              "openai/gpt-5", "opencode/claude-sonnet"]

    def test_easy_work_takes_the_smallest_local_coder(self):
        decision = route("rename a variable", self.MODELS, kind="code")
        self.assertEqual((decision["model"], decision["tier"], decision["needs_approval"]),
                         ("ollama/qwen2.5-coder:7b", "local", False))

    def test_hard_work_takes_the_largest_local_model(self):
        decision = route("refactor the architecture " + "x" * 3500, self.MODELS, kind="code", files=8)
        self.assertEqual(decision["difficulty"], "hard")
        self.assertEqual(decision["model"], "ollama/qwen2.5-coder:32b")

    def test_free_is_used_when_no_local_model_exists(self):
        self.assertEqual(route("hi", ["opencode/big-pickle-free", "openai/gpt-5"])["tier"], "free")

    def test_paid_is_never_chosen_automatically(self):
        paid_only = ["openai/gpt-5"]
        none = route("hi", paid_only)
        self.assertIsNone(none["model"])
        self.assertTrue(none["needs_approval"])
        suggested = route("hi", paid_only, allow_paid=True)
        self.assertEqual((suggested["model"], suggested["needs_approval"]), ("openai/gpt-5", True))

    def test_failing_model_is_skipped_after_enough_runs(self):
        stats = {"ollama/qwen2.5-coder:7b": {"runs": 4, "failures": 4},
                 "ollama/qwen2.5-coder:32b": {"runs": 1, "failures": 1}}  # too few runs to judge
        decision = route("rename a variable", self.MODELS, kind="code", stats=stats)
        self.assertEqual(decision["model"], "ollama/qwen2.5-coder:32b")
        self.assertIn("cooling down", decision["reason"])

    def test_role_floor_and_validation(self):
        self.assertEqual(estimate_difficulty("tiny", role="reviewer"), "medium")
        self.assertEqual(estimate_difficulty("tiny", role="reporter"), "easy")
        with self.assertRaises(ValueError):
            route("x", self.MODELS, role="boss")
        with self.assertRaises(ValueError):
            route("x", self.MODELS, kind="video")


class QueueFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.q = Queue(Path(self.temp.name) / "jobs.db")

    def tearDown(self):
        self.temp.cleanup()

    def finish_done(self, job_id):
        self.q.finish(job_id, {"ok": True})
        self.q.approve(job_id)

    def test_dependent_job_waits_until_dependency_is_done(self):
        first = self.q.add("code", "first")
        second = self.q.add("code", "second", depends_on=[first])
        self.assertEqual(self.q.claim()["id"], first)
        self.assertIsNone(self.q.claim())  # first running, second blocked
        self.assertEqual(self.q.waiting_on(second), {first: "running"})
        self.q.finish(first, {"ok": True})
        self.assertIsNone(self.q.claim())  # review is not done yet
        self.q.approve(first)
        self.assertEqual(self.q.claim()["id"], second)
        self.assertEqual(self.q.waiting_on(second), {})

    def test_independent_job_is_not_blocked_by_a_waiting_one(self):
        first = self.q.add("code", "first", require_approval=True)
        self.q.add("code", "second", depends_on=[first])
        free = self.q.add("code", "free")
        self.assertEqual(self.q.claim()["id"], free)

    def test_failed_dependency_blocks_dependents_until_retried(self):
        first = self.q.add("code", "first")
        second = self.q.add("code", "second", depends_on=[first])
        self.q.claim()
        self.q.finish(first, success=False)
        self.assertIsNone(self.q.claim())
        self.assertEqual(self.q.waiting_on(second), {first: "failed"})
        self.q.retry(first)
        self.assertEqual(self.q.claim()["id"], first)

    def test_bad_dependencies_are_rejected(self):
        with self.assertRaises(ValueError):
            self.q.add("code", "x", depends_on=["missing"])
        first = self.q.add("code", "first")
        with self.assertRaises(ValueError):
            self.q.add("code", "x", depends_on=[first, first])

    def test_concurrency_cap_applies_across_workers(self):
        for i in range(4):
            self.q.add("code", "job %d" % i)
        results, lock = [], threading.Lock()

        def grab():
            job = Queue(self.q.path).claim(max_running=2)
            with lock:
                results.append(job)

        threads = [threading.Thread(target=grab) for _ in range(4)]
        for t in threads: t.start()
        for t in threads: t.join(30)
        self.assertEqual(len([r for r in results if r]), 2)
        self.assertEqual(len([j for j in self.q.list() if j["state"] == "running"]), 2)

    def test_cancel_waiting_and_running_jobs(self):
        waiting = self.q.add("code", "waiting")
        self.assertEqual(self.q.cancel(waiting), "cancelled")
        self.assertIsNone(self.q.claim())
        running = self.q.add("code", "running")
        self.q.claim()
        self.assertEqual(self.q.cancel(running), "cancel_requested")
        self.assertEqual(self.q.finish(running, {"ok": True}), "cancelled")  # result does not resurrect it
        with self.assertRaises(ValueError):
            self.q.cancel("unknown")

    def test_retry_respects_attempt_limit_unless_forced(self):
        job = self.q.add("code", "flaky")
        for _ in range(2):  # max_attempts defaults to 2
            self.q.claim()
            self.q.finish(job, success=False)
            if _ == 0:
                self.q.retry(job)
        with self.assertRaises(ValueError):
            self.q.retry(job)
        self.q.retry(job, force=True)
        self.assertEqual(self.q.list()[0]["state"], "queued")
        with self.assertRaises(ValueError):
            self.q.retry(self.q.add("code", "never ran"))  # only failed/cancelled

    def test_checkpoint_survives_reopening_the_queue(self):
        job = self.q.add("code", "long")
        with self.assertRaises(ValueError):
            self.q.checkpoint(job, {"step": 1})  # not running yet
        self.q.claim()
        self.q.checkpoint(job, {"step": 2, "files": ["a.py"]})
        self.assertEqual(Queue(self.q.path).checkpoint(job), {"step": 2, "files": ["a.py"]})
        self.assertEqual(self.q.recover(), 1)  # interrupted, never replayed
        self.assertEqual(self.q.checkpoint(job)["step"], 2)  # progress kept for the operator

    def test_old_database_gains_new_columns(self):
        import sqlite3
        path = Path(self.temp.name) / "old.db"
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, state TEXT, payload TEXT,"
                   " attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2, result TEXT)")
        db.execute("INSERT INTO jobs(id,kind,title,state,payload) VALUES('old','code','t','queued','{}')")
        db.commit(); db.close()
        q = Queue(path)
        self.assertEqual(q.claim()["id"], "old")


if __name__ == "__main__":
    unittest.main()


class CooldownAndSpreadTests(unittest.TestCase):
    MODELS = ["ollama/a-3b", "ollama/b-3b"]

    def stats(self, **entries):
        full = {m: {} for m in self.MODELS}                      # every model has history, so only the entries differ
        full.update(entries)
        return {m: {"runs": 5, "failures": 0, "streak_fail": 0, "last_ts": 0, **e} for m, e in full.items()}

    def test_a_just_failed_model_cools_down_then_comes_back(self):
        fail_now = self.stats(**{"ollama/a-3b": {"streak_fail": 1, "last_ts": 1000}})
        self.assertEqual(route("hi", self.MODELS, stats=fail_now, now=1030)["model"], "ollama/b-3b")
        self.assertEqual(route("hi", self.MODELS, stats=fail_now, now=1100)["model"], "ollama/a-3b")   # after 60 s

    def test_repeated_failures_cool_down_longer(self):
        stats = self.stats(**{"ollama/a-3b": {"streak_fail": 2, "last_ts": 1000}})
        self.assertEqual(route("hi", self.MODELS, stats=stats, now=1300)["model"], "ollama/b-3b")
        self.assertEqual(route("hi", self.MODELS, stats=stats, now=1700)["model"], "ollama/a-3b")

    def test_everything_cooling_down_is_reported_not_guessed(self):
        stats = self.stats(**{m: {"streak_fail": 3, "last_ts": 1000} for m in self.MODELS})
        self.assertIsNone(route("hi", self.MODELS, stats=stats, now=1010)["model"])

    def test_equal_models_share_the_load(self):
        stats = self.stats(**{"ollama/a-3b": {"runs": 9}, "ollama/b-3b": {"runs": 2}})
        self.assertEqual(route("hi", self.MODELS, stats=stats)["model"], "ollama/b-3b")

    def test_run_stats_track_the_failure_streak(self):
        import tempfile
        from pathlib import Path
        from workshop.runstats import RunStats
        with tempfile.TemporaryDirectory() as d:
            rs = RunStats(Path(d) / "r.jsonl")
            for ok, ts in ((False, 1), (False, 2), (True, 3), (False, 4)):
                rs.record("m/x", ok=ok, seconds=1, when=ts)
            entry = rs.load(now=10)["m/x"]
            self.assertEqual((entry["streak_fail"], entry["last_ts"], entry["runs"]), (1, 4, 4))
