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

    def test_removed_image_kind_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                Queue(Path(d) / "tasks.db").add("image", "Generate sprite")

    def test_retry_limit(self):
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            task = q.add("code", "Generate sprite")
            self.assertEqual(q.claim()["id"], task)
            self.assertEqual(q.finish(task, success=False), "failed")
            self.assertIsNone(q.claim())

    def test_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            q.add("qa", "Run tests")
            q.claim()
            self.assertEqual(q.recover(), 1)
            self.assertIsNone(q.claim())


class TestStaleRecovery(unittest.TestCase):
    def test_live_worker_job_survives_stale_recovery(self):
        import time
        with tempfile.TemporaryDirectory() as d:
            q = Queue(Path(d) / "tasks.db")
            live = q.add("code", "live")
            dead = q.add("code", "dead")
            q.claim(); q.claim()
            with q._db() as db:
                db.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (time.time() - 600, dead))
            q.heartbeat(live)
            self.assertEqual(q.recover(stale_after=120), 1)
            states = {j["id"]: j["state"] for j in q.list()}
            self.assertEqual(states[live], "running")
            self.assertEqual(states[dead], "failed")

    def test_old_database_is_migrated(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "old.db"
            db = sqlite3.connect(path)
            db.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, title TEXT, state TEXT, "
                       "payload TEXT, attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2, result TEXT)")
            db.execute("INSERT INTO jobs(id,kind,title,state,payload) VALUES('a','code','t','running','{}')")
            db.commit(); db.close()
            q = Queue(path)
            self.assertEqual(q.recover(stale_after=120), 1)


class TestConcurrencyAndSafety(unittest.TestCase):
    def make(self, d):
        return Queue(Path(d) / "tasks.db")

    def test_concurrent_claims_never_double_run_a_job(self):
        import threading
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            ids = {q.add("code", "job %d" % i) for i in range(12)}
            claimed, errors, lock = [], [], threading.Lock()

            def worker():
                try:
                    while True:
                        job = Queue(q.path).claim()
                        if job is None:
                            return
                        with lock:
                            claimed.append(job["id"])
                except Exception as error:  # surfaced below
                    errors.append(error)

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads: t.start()
            for t in threads: t.join(60)
            self.assertEqual(errors, [])
            self.assertEqual(sorted(claimed), sorted(ids))  # each exactly once
            self.assertTrue(all(j["attempts"] == 1 and j["state"] == "running" for j in q.list()))

    def test_pending_approval_is_never_claimed_until_authorized(self):
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            task = q.add("code", "needs approval", require_approval=True)
            self.assertIsNone(q.claim())
            q.authorize(task)
            self.assertEqual(q.claim()["id"], task)

    def test_late_finish_cannot_resurrect_a_recovered_job(self):
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            task = q.add("code", "slow")
            q.claim()
            self.assertEqual(q.recover(), 1)
            with self.assertRaises(ValueError):
                q.finish(task, {"ok": True})
            q.heartbeat(task)  # must not flip a failed job back to running
            job = q.list()[0]
            self.assertEqual(job["state"], "failed")
            self.assertIn("Stale", job["result"])
            self.assertIsNone(q.claim())  # recovered jobs are never replayed automatically

    def test_recovery_only_touches_running_jobs(self):
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            queued = q.add("code", "queued")
            waiting = q.add("code", "waiting", require_approval=True)
            reviewed = q.add("qa", "reviewed")
            q.claim(kinds=["qa"])
            q.finish(reviewed, {"ok": True})
            self.assertEqual(q.recover(), 0)
            self.assertEqual(q.recover(stale_after=0), 0)
            states = {j["id"]: j["state"] for j in q.list()}
            self.assertEqual(states, {queued: "queued", waiting: "pending_approval", reviewed: "review"})

    def test_job_without_heartbeat_is_stale(self):
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            task = q.add("code", "legacy row")
            q.claim()
            with q._db() as db:
                db.execute("UPDATE jobs SET heartbeat=NULL WHERE id=?", (task,))
            self.assertEqual(q.recover(stale_after=120), 1)

    def test_defer_does_not_consume_an_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            q = self.make(d)
            task = q.add("code", "deferred")
            q.claim()
            q.defer(task)
            self.assertEqual((q.list()[0]["state"], q.list()[0]["attempts"]), ("queued", 0))
            with self.assertRaises(ValueError):
                q.defer(task)  # no longer running
