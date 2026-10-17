"""Explicitly approved ComfyUI workflow submission (local-only).

This adapter submits API-format JSON to a trusted local ComfyUI instance.
It does not download models, install custom nodes, or wait for completion.
"""
import json
import secrets
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

MAX_WORKFLOW_BYTES = 2 * 1024 * 1024

def submit_workflow(workflow_path, *, enabled=False, base_url="http://127.0.0.1:8188", timeout=20):
    if not enabled:
        raise PermissionError("ComfyUI submission requires explicit opt-in")
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("ComfyUI URL must be a local HTTP origin")
    path = Path(workflow_path).expanduser().resolve(strict=True)
    if not path.is_file() or path.stat().st_size > MAX_WORKFLOW_BYTES:
        raise ValueError("Workflow must be a JSON file smaller than 2 MiB")
    workflow = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(workflow, dict) or not workflow:
        raise ValueError("Workflow must be a nonempty API-format JSON object")
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or not isinstance(node.get("class_type"), str) or not isinstance(node.get("inputs"), dict):
            raise ValueError(f"Invalid API-format workflow node: {node_id}")
    body = json.dumps({"prompt": workflow, "client_id": secrets.token_hex(16)}).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + "/prompt", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict) or not isinstance(data.get("prompt_id"), str):
        raise ValueError("ComfyUI did not return a prompt_id")
    return {"prompt_id": data["prompt_id"], "status": "submitted"}
