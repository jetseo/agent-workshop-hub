"""Single-job opt-in worker. No automatic retries of side-effecting commands."""
import argparse
import json
import signal
import threading
import time
from pathlib import Path
from .queue import Queue
from .adapters import run_opencode

def _defer(queue, job):
    # A disabled executor must not consume or fail a queued task.
    queue.defer(job["id"])
    return {"job_id": job["id"], "state": "queued", "reason": "executor disabled"}

HEARTBEAT_SECONDS = 10.0
STALE_AFTER_SECONDS = 120.0

def _beat(queue, job_id, stop):
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            queue.heartbeat(job_id)
        except Exception:
            pass

def process_one(queue, *, allow_opencode=False, max_running=None):
    if not allow_opencode:
        raise PermissionError("Worker requires an explicit executor opt-in")
    # Claim only jobs with an enabled executor.
    enabled_kinds = []
    if allow_opencode:
        enabled_kinds.append("code")
    job = queue.claim(kinds=enabled_kinds, max_running=max_running)
    if job is None:
        return None
    stop_beat = threading.Event()
    try:
        threading.Thread(target=_beat, args=(queue, job["id"], stop_beat), daemon=True).start()
        return _execute(queue, job, allow_opencode)
    finally:
        stop_beat.set()

def _execute(queue, job, allow_opencode):
    try:
        payload = json.loads(job["payload"])
        if job["kind"] == "code":
            if not allow_opencode:
                return _defer(queue, job)
            workspace = payload.get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise ValueError("Code job requires payload.workspace")
            result = run_opencode(job["title"], workspace, enabled=True)
        else:
            raise ValueError("Unsupported executor kind: " + job["kind"])
        if result.get("exit_code", 0) != 0:
            # Do not retry a potentially side-effecting command automatically.
            queue.finish(job["id"], result, success=False)
            return {"job_id": job["id"], "state": "failed", "result": result}
        state = queue.finish(job["id"], result, success=True)
        return {"job_id": job["id"], "state": state, "result": result}
    except (KeyboardInterrupt, SystemExit):
        # Interrupted mid-run: the side effects are unknown, so fail the job for
        # explicit operator review instead of leaving it 'running' or replaying it.
        try:
            queue.finish(job["id"], {"error": "Worker interrupted during execution; "
                                     "review side effects before re-queuing",
                                     "type": "Interrupted"}, success=False)
        finally:
            raise
    except Exception as error:
        # Preserve the original executor failure even if persistence also fails.
        detail = {"error": str(error), "type": type(error).__name__}
        try:
            queue.finish(job["id"], detail, success=False)
        except Exception as storage_error:
            detail["storage_error"] = str(storage_error)
        return {"job_id": job["id"], "state": "failed", "result": detail}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    parser.add_argument("--allow-opencode", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new tasks")
    parser.add_argument("--recover-stale", action="store_true", help="Mark running jobs failed if their worker stopped heartbeating (>120s); live workers are untouched")
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--max-running", type=int, default=None, help="Cap on jobs running at once across all workers sharing the database")
    args = parser.parse_args()
    if not args.allow_opencode:
        parser.error("Pass --allow-opencode after reviewing permissions")
    queue = Queue(args.db)
    if args.interval < 0.5:
        parser.error("--interval must be at least 0.5 seconds")
    # Startup recovery is operator-controlled to avoid marking another live worker's
    # running tasks failed when multiple processes share the same database.
    if args.recover_stale:
        print(json.dumps({"marked_failed": queue.recover(stale_after=STALE_AFTER_SECONDS)}), flush=True)
    # Treat SIGTERM like Ctrl+C so interrupted jobs are marked failed, not left running.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        while True:
            outcome = process_one(queue, allow_opencode=args.allow_opencode,
                                  max_running=args.max_running)
            if outcome is not None:
                print(json.dumps(outcome, ensure_ascii=False), flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Worker stopped.", flush=True)

if __name__ == "__main__":
    main()
