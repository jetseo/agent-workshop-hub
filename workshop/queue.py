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
            for name, ddl in (("depends_on", "TEXT DEFAULT '[]'"), ("checkpoint", "TEXT"),
                              ("cancel_requested", "INTEGER DEFAULT 0")):
                if name not in columns:
                    db.execute("ALTER TABLE jobs ADD COLUMN " + name + " " + ddl)

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

    def add(self, kind, title, payload=None, *, require_approval=False, depends_on=()):
        if (kind not in ("plan", "code", "qa") or not isinstance(title, str)
                or not title.strip() or len(title) > 500):
            raise ValueError("Invalid task")
        deps = list(depends_on)
        job_id = str(uuid.uuid4())
        state = "pending_approval" if require_approval else "queued"
        with self._db() as db:
            known = {row["id"] for row in db.execute("SELECT id FROM jobs")}
            # Only existing jobs can be dependencies, so cycles are impossible.
            if len(set(deps)) != len(deps) or any(dep not in known for dep in deps):
                raise ValueError("Unknown or duplicate dependency")
            db.execute("INSERT INTO jobs(id,kind,title,state,payload,depends_on) VALUES(?,?,?,?,?,?)",
                       (job_id, kind, title, state, json.dumps(payload or {}), json.dumps(deps)))
        return job_id

    def claim(self, kinds=None, *, max_running=None):
        """Take the oldest queued job whose dependencies are all done.

        max_running caps concurrently running jobs across every worker sharing the database.
        """
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            allowed = tuple(kinds) if kinds is not None else None
            if allowed is not None:
                if not allowed:
                    return None
                if any(kind not in ("code", "plan", "qa") for kind in allowed):
                    raise ValueError("Invalid kind filter")
            if max_running is not None:
                running = db.execute("SELECT COUNT(*) FROM jobs WHERE state='running'").fetchone()[0]
                if running >= max_running:
                    return None
            if allowed is not None:
                placeholders = ",".join("?" for _ in allowed)
                rows = db.execute("SELECT * FROM jobs WHERE state='queued' AND kind IN (" +
                                  placeholders + ") ORDER BY rowid", allowed).fetchall()
            else:
                rows = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY rowid").fetchall()
            row = next((r for r in rows if self._deps_done(db, r)), None)
            if row is None:
                return None
            db.execute("UPDATE jobs SET state='running', attempts=attempts+1, heartbeat=? WHERE id=?",
                       (time.time(), row["id"]))
            return dict(db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone())

    @staticmethod
    def _deps_done(db, row):
        deps = json.loads(row["depends_on"] or "[]")
        if not deps:
            return True
        marks = ",".join("?" for _ in deps)
        done = db.execute("SELECT COUNT(*) FROM jobs WHERE state='done' AND id IN (" + marks + ")",
                          deps).fetchone()[0]
        return done == len(deps)

    def waiting_on(self, job_id):
        """Dependencies that are not done yet, with their states (explains a stuck job)."""
        with self._db() as db:
            row = db.execute("SELECT depends_on FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("Unknown task")
            out = {}
            for dep in json.loads(row["depends_on"] or "[]"):
                found = db.execute("SELECT state FROM jobs WHERE id=?", (dep,)).fetchone()
                if found is None or found["state"] != "done":
                    out[dep] = found["state"] if found else "missing"
            return out

    def checkpoint(self, job_id, data=None):
        """Save (data given) or read a job's progress so an operator can resume from it."""
        with self._db() as db:
            if data is None:
                row = db.execute("SELECT checkpoint FROM jobs WHERE id=?", (job_id,)).fetchone()
                if row is None:
                    raise ValueError("Unknown task")
                return json.loads(row["checkpoint"]) if row["checkpoint"] else None
            cur = db.execute("UPDATE jobs SET checkpoint=? WHERE id=? AND state='running'",
                             (json.dumps(data), job_id))
            if cur.rowcount != 1:
                raise ValueError("Task not running")

    def cancel(self, job_id):
        """Waiting jobs are cancelled at once; a running one is flagged and ends as cancelled when
        its worker next reports (the executor process itself is not killed)."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            cur = db.execute("UPDATE jobs SET state='cancelled' WHERE id=? "
                             "AND state IN ('queued','pending_approval','failed','review')", (job_id,))
            if cur.rowcount == 1:
                return "cancelled"
            cur = db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=? AND state='running'", (job_id,))
            if cur.rowcount == 1:
                return "cancel_requested"
            raise ValueError("Task cannot be cancelled")

    def retry(self, job_id, *, force=False):
        """Operator-initiated re-queue of a failed or cancelled job; respects max_attempts unless force."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE id=? AND state IN ('failed','cancelled')",
                             (job_id,)).fetchone()
            if row is None:
                raise ValueError("Only failed or cancelled tasks can be retried")
            if not force and row["attempts"] >= row["max_attempts"]:
                raise ValueError("Attempt limit reached; review side effects, then retry with force")
            db.execute("UPDATE jobs SET state='queued', cancel_requested=0, heartbeat=NULL WHERE id=?",
                       (job_id,))

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
            if row["cancel_requested"]:
                state = "cancelled"
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
