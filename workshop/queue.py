"""SQLite task queue: state management only, never executes AI tools."""
import json
import time
from contextlib import contextmanager
import sqlite3
import uuid
from pathlib import Path

class Queue:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs
                (id TEXT PRIMARY KEY, kind TEXT, title TEXT, state TEXT,
                 payload TEXT, attempts INTEGER DEFAULT 0, max_attempts INTEGER DEFAULT 2,
                 result TEXT)""")
            # Additive migration for databases created before heartbeats existed.
            columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)")}
            if "heartbeat" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN heartbeat REAL")

    @contextmanager
    def _db(self):
        """Commit or roll back and always close SQLite connections."""
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def add(self, kind, title, payload=None, *, require_approval=False):
        if (kind not in ("plan", "code", "image", "qa") or not isinstance(title, str)
                or not title.strip() or len(title) > 500):
            raise ValueError("Invalid task")
        job_id = str(uuid.uuid4())
        state = "pending_approval" if require_approval else "queued"
        with self._db() as db:
            db.execute("INSERT INTO jobs(id,kind,title,state,payload) VALUES(?,?,?,?,?)",
                       (job_id, kind, title, state, json.dumps(payload or {})))
        return job_id

    def claim(self, kinds=None):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            allowed = tuple(kinds) if kinds is not None else None
            if allowed is not None:
                if not allowed:
                    return None
                if any(kind not in ("code", "image", "plan", "qa") for kind in allowed):
                    raise ValueError("Invalid kind filter")
                placeholders = ",".join("?" for _ in allowed)
                row = db.execute(
                    "SELECT * FROM jobs WHERE state='queued' AND kind IN (" +
                    placeholders + ") ORDER BY rowid LIMIT 1", allowed).fetchone()
            else:
                row = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', attempts=attempts+1, heartbeat=? WHERE id=?",
                       (time.time(), row["id"]))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    def heartbeat(self, job_id):
        """Mark a running job as still owned by a live worker."""
        with self._db() as db:
            db.execute("UPDATE jobs SET heartbeat=? WHERE id=? AND state='running'",
                       (time.time(), job_id))

    def defer(self, job_id):
        """Return an unexecuted job to the queue without consuming an attempt."""
        with self._db() as db:
            cur = db.execute(
                "UPDATE jobs SET state='queued', attempts=attempts-1 "
                "WHERE id=? AND state='running' AND attempts>0", (job_id,))
            if cur.rowcount != 1:
                raise ValueError("Task not running")

    def finish(self, job_id, result=None, success=True):
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=? AND state='running'", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Task not running")
            state = "review" if success else "failed"
            db.execute("UPDATE jobs SET state=?, result=? WHERE id=?", (state, json.dumps(result), job_id))
            return state

    def authorize(self, job_id):
        """Approve a pending task for execution, not the completed result."""
        with self._db() as db:
            cur = db.execute(
                "UPDATE jobs SET state='queued' WHERE id=? AND state='pending_approval'",
                (job_id,))
            if cur.rowcount != 1:
                raise ValueError("Task not awaiting execution approval")

    def approve(self, job_id):
        with self._db() as db:
            cur = db.execute("UPDATE jobs SET state='done' WHERE id=? AND state='review'", (job_id,))
            if cur.rowcount != 1:
                raise ValueError("Task not awaiting review")

    def list(self):
        with self._db() as db:
            return [dict(row) for row in db.execute("SELECT * FROM jobs ORDER BY rowid")]

    def recover(self, stale_after=None):
        """Fail interrupted tasks; never replay them automatically.

        With stale_after (seconds), only jobs whose worker stopped sending
        heartbeats are failed, so a live worker sharing the database is safe.
        Without it, every running job is failed (single-worker legacy mode).
        """
        message = json.dumps({"error": "Worker stopped before completion; review side effects before re-queuing",
                              "type": "Stale"})
        with self._db() as db:
            if stale_after is None:
                return db.execute("UPDATE jobs SET state='failed', result=? WHERE state='running'",
                                  (message,)).rowcount
            cutoff = time.time() - stale_after
            return db.execute(
                "UPDATE jobs SET state='failed', result=? WHERE state='running' "
                "AND (heartbeat IS NULL OR heartbeat<?)", (message, cutoff)).rowcount
