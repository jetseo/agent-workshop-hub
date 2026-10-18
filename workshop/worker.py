"""Single-job opt-in worker. No automatic retries of side-effecting commands."""
import argparse
import json
import re
import signal
import time
from pathlib import Path
from .queue import Queue
from .adapters import run_opencode
from .comfyui import submit_workflow
from .comfyui_status import wait_for_completion
from scripts.comfyui_smoke import wait_for_images, download_images, save_report

def _defer(queue, job):
    # A disabled executor must not consume or fail a queued task.
    queue.defer(job["id"])
    return {"job_id": job["id"], "state": "queued", "reason": "executor disabled"}

def process_one(queue, *, allow_opencode=False, allow_comfyui=False):
    if not (allow_opencode or allow_comfyui):
        raise PermissionError("Worker requires an explicit executor opt-in")
    # Claim only jobs with an enabled executor.
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
            if payload.get("save_outputs") is True:
                output_dir = payload.get("output_dir")
                if (not isinstance(output_dir, str) or not output_dir.strip()
                        or not Path(output_dir).is_absolute()):
                    raise ValueError("Image output saving requires an absolute payload.output_dir")
            submitted = submit_workflow(workflow, enabled=True)
            # Reject malformed identifiers before any polling, downloads or report writes.
            prompt_id = submitted.get("prompt_id") if isinstance(submitted, dict) else None
            if not isinstance(prompt_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", prompt_id):
                raise ValueError("Invalid ComfyUI prompt identifier")
            if payload.get("save_outputs") is True:
                images = wait_for_images(submitted["prompt_id"], timeout=300)
                files = download_images(images, base_url="http://127.0.0.1:8188",
                                        output_dir=output_dir)
                report = {"prompt_id": submitted["prompt_id"], "state": "completed",
                          "files": files}
                report_path = save_report(report, Path(output_dir) /
                                          ("report-" + prompt_id + ".json"))
                result = {**report, "report_path": str(report_path)}
            else:
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
    parser.add_argument("--allow-comfyui", action="store_true")
    parser.add_argument("--watch", action="store_true", help="Keep polling for new tasks")
    parser.add_argument("--recover-stale", action="store_true", help="Mark ALL running jobs failed; use only when no other worker is active")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()
    if not (args.allow_opencode or args.allow_comfyui):
        parser.error("Pass --allow-opencode or --allow-comfyui after reviewing permissions")
    queue = Queue(args.db)
    if args.interval < 0.5:
        parser.error("--interval must be at least 0.5 seconds")
    # Startup recovery is operator-controlled to avoid marking another live worker's
    # running tasks failed when multiple processes share the same database.
    if args.recover_stale:
        print(json.dumps({"marked_failed": queue.recover()}), flush=True)
    # Treat SIGTERM like Ctrl+C so interrupted jobs are marked failed, not left running.
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
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
