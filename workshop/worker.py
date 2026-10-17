"""Single-job opt-in worker. No automatic retries of side-effecting commands."""
import argparse
import json
import time
from pathlib import Path
from .queue import Queue
from .adapters import run_opencode
from .comfyui import submit_workflow
from .comfyui_status import wait_for_completion

def _defer(queue, job):
    # A disabled executor must not consume or fail a queued task.
    queue.defer(job["id"])
    return {"job_id": job["id"], "state": "queued", "reason": "executor disabled"}

def process_one(queue, *, allow_opencode=False, allow_comfyui=False):
    if not (allow_opencode or allow_comfyui):
        raise PermissionError("Worker requires an explicit executor opt-in")
    # Recover stale tasks explicitly before starting a new worker; never replay them.
    # Queue selection is FIFO; unsupported kinds must not be silently executed.
    enabled_kinds = []
    if allow_opencode:
        enabled_kinds.append("code")
    if allow_comfyui:
        enabled_kinds.append("image")
    job = queue.claim(kinds=enabled_kinds)
    if job is None:
        return None
    try:
        payload = json.loads(job["payload"])
        if job["kind"] == "code":
            if not allow_opencode:
                return _defer(queue, job)
            workspace = payload.get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise ValueError("Code job requires payload.workspace")
            result = run_opencode(job["title"], workspace, enabled=True)
        elif job["kind"] == "image":
            if not allow_comfyui:
                return _defer(queue, job)
            workflow = payload.get("workflow")
            if not isinstance(workflow, str) or not workflow:
                raise ValueError("Image job requires payload.workflow")
            submitted = submit_workflow(workflow, enabled=True)
            result = wait_for_completion(submitted["prompt_id"], max_wait=300)
            if result["state"] != "completed":
                queue.finish(job["id"], result, success=False)
                return {"job_id": job["id"], "state": "failed", "result": result}
        else:
            raise ValueError("Unsupported executor kind: " + job["kind"])
        if result.get("exit_code", 0) != 0:
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
    parser.add_argument("--allow-comfyui", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new tasks")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    if not (args.allow_opencode or args.allow_comfyui):
        parser.error("Pass --allow-opencode or --allow-comfyui after reviewing permissions")
    queue = Queue(args.db)
    if args.interval < 0.5:
        parser.error("--interval must be at least 0.5 seconds")
    queue.recover()
    try:
        while True:
            outcome = process_one(queue, allow_opencode=args.allow_opencode,
                                  allow_comfyui=args.allow_comfyui)
            if outcome is not None:
                print(json.dumps(outcome, ensure_ascii=False), flush=True)
            if not args.watch:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("Worker stopped.", flush=True)

if __name__ == "__main__":
    main()
