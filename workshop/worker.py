"""Single-job opt-in worker. No automatic retries of side-effecting commands."""
import argparse
import json
from pathlib import Path
from .queue import Queue
from .adapters import run_opencode

def process_one(queue, *, allow_opencode=False):
    if not allow_opencode:
        raise PermissionError("Worker requires --allow-opencode; it may modify files and incur costs")
    # Recover stale tasks explicitly before starting a new worker; never replay them.
    job = queue.claim()
    if job is None:
        return None
    try:
        payload = json.loads(job["payload"])
        if job["kind"] != "code":
            raise ValueError("Only code jobs are supported; image/qa/plan executors are not connected")
        workspace = payload.get("workspace")
        if not isinstance(workspace, str) or not workspace:
            raise ValueError("Code job requires payload.workspace")
        result = run_opencode(job["title"], workspace, enabled=True)
        if result["exit_code"] != 0:
            # Do not retry a potentially side-effecting command automatically.
            queue.finish(job["id"], result, success=False)
            return {"job_id": job["id"], "state": "failed", "result": result}
        state = queue.finish(job["id"], result, success=True)
        return {"job_id": job["id"], "state": state, "result": result}
    except Exception as error:
        # Always transition away from running on handled errors.
        queue.finish(job["id"], {"error": str(error)}, success=False)
        return {"job_id": job["id"], "error": str(error)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    parser.add_argument("--allow-opencode", action="store_true")
    args = parser.parse_args()
    if not args.allow_opencode:
        parser.error("Pass --allow-opencode after reviewing cost and filesystem permissions")
    queue = Queue(args.db)
    queue.recover()
    print(json.dumps(process_one(queue, allow_opencode=True), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
