"""Single-job opt-in worker. No automatic retries of side-effecting commands."""
import argparse
import json
from pathlib import Path
from .queue import Queue
from .adapters import run_opencode
from .comfyui import submit_workflow

def process_one(queue, *, allow_opencode=False, allow_comfyui=False):
    if not (allow_opencode or allow_comfyui):
        raise PermissionError("Worker requires an explicit executor opt-in")
    # Recover stale tasks explicitly before starting a new worker; never replay them.
    job = queue.claim()
    if job is None:
        return None
    try:
        payload = json.loads(job["payload"])
        if job["kind"] == "code":
            if not allow_opencode:
                raise PermissionError("Code execution was not enabled")
            workspace = payload.get("workspace")
            if not isinstance(workspace, str) or not workspace:
                raise ValueError("Code job requires payload.workspace")
            result = run_opencode(job["title"], workspace, enabled=True)
        elif job["kind"] == "image":
            if not allow_comfyui:
                raise PermissionError("Image submission was not enabled")
            workflow = payload.get("workflow")
            if not isinstance(workflow, str) or not workflow:
                raise ValueError("Image job requires payload.workflow")
            result = submit_workflow(workflow, enabled=True)
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
    args = parser.parse_args()
    if not (args.allow_opencode or args.allow_comfyui):
        parser.error("Pass --allow-opencode or --allow-comfyui after reviewing permissions")
    queue = Queue(args.db)
    queue.recover()
    print(json.dumps(process_one(queue, allow_opencode=args.allow_opencode, allow_comfyui=args.allow_comfyui), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
