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
