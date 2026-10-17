"""Poll a local ComfyUI prompt until it finishes and validate output image metadata."""
import argparse
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from workshop.comfyui import submit_workflow


def wait_for_images(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=180):
    if not isinstance(prompt_id, str) or not prompt_id:
        raise ValueError("Invalid prompt ID")
    deadline = time.monotonic() + timeout
    url = base_url.rstrip("/") + "/history/" + quote(prompt_id, safe="")
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                history = json.load(response)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"ComfyUI history unavailable: {exc}") from exc
        entry = history.get(prompt_id) if isinstance(history, dict) else None
        if isinstance(entry, dict):
            status = entry.get("status") or {}
            if status.get("status_str") == "error" or status.get("completed") is False and status.get("status_str") == "error":
                raise RuntimeError("ComfyUI reported a failed workflow")
            if status.get("completed") is True:
                images = []
                for node in (entry.get("outputs") or {}).values():
                    if not isinstance(node, dict):
                        continue
                    for item in node.get("images", []):
                        if isinstance(item, dict) and isinstance(item.get("filename"), str):
                            images.append({"filename": item["filename"], "subfolder": item.get("subfolder", ""), "type": item.get("type", "")})
                if not images:
                    raise RuntimeError("Workflow completed without image outputs")
                return images
        time.sleep(2)
    raise TimeoutError(f"ComfyUI prompt did not finish within {timeout}s")


def main():
    parser = argparse.ArgumentParser(description="Explicit local ComfyUI image generation smoke test")
    parser.add_argument("workflow", help="API-format ComfyUI workflow JSON file")
    parser.add_argument("--port", type=int, default=8188)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--submit", action="store_true", help="Actually submit workflow")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535 or not 1 <= args.timeout <= 3600:
        parser.error("Invalid port or timeout")
    if not args.submit:
        print("[dry-run] Would submit workflow to localhost and wait for image output metadata.")
        return
    base = f"http://127.0.0.1:{args.port}"
    result = submit_workflow(args.workflow, enabled=True, base_url=base)
    print("Submitted prompt:", result["prompt_id"])
    images = wait_for_images(result["prompt_id"], base_url=base, timeout=args.timeout)
    print(json.dumps({"prompt_id": result["prompt_id"], "images": images}, indent=2))


if __name__ == "__main__":
    main()
