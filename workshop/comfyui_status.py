"""Read-only ComfyUI history polling and output metadata inspection."""
import json
import time
import urllib.error
import urllib.request
from urllib.parse import quote, urlparse

def _origin(base_url):
    parsed = urlparse(base_url)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in ("", "/")):
        raise ValueError("Only local ComfyUI HTTP origins are allowed")
    return base_url.rstrip("/")

def history(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=10):
    if not isinstance(prompt_id, str) or not prompt_id or len(prompt_id) > 200:
        raise ValueError("Invalid prompt ID")
    url = _origin(base_url) + "/history/" + quote(prompt_id, safe="")
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise ValueError("Unexpected ComfyUI history response")
    return data.get(prompt_id)

def inspect(prompt_id, *, base_url="http://127.0.0.1:8188", timeout=10):
    item = history(prompt_id, base_url=base_url, timeout=timeout)
    if item is None:
        return {"prompt_id": prompt_id, "state": "pending", "outputs": []}
    status = item.get("status", {})
    messages = status.get("messages", [])
    if status.get("status_str") == "error" or any(
        isinstance(message, (list, tuple)) and message and message[0] == "execution_error"
        for message in messages
    ):
        return {"prompt_id": prompt_id, "state": "failed", "outputs": []}
    if not status.get("completed", False):
        return {"prompt_id": prompt_id, "state": "pending", "outputs": []}
    outputs = []
    for node_id, node in item.get("outputs", {}).items():
        for image in node.get("images", []):
            if isinstance(image, dict):
                outputs.append({"node_id": node_id, "filename": image.get("filename"),
                                "subfolder": image.get("subfolder", ""), "type": image.get("type")})
    return {"prompt_id": prompt_id, "state": "completed", "outputs": outputs}

def wait_for_completion(prompt_id, *, base_url="http://127.0.0.1:8188",
                        max_wait=300, interval=3):
    if max_wait <= 0 or interval <= 0:
        raise ValueError("Polling intervals must be positive")
    deadline = time.monotonic() + max_wait
    while True:
        result = inspect(prompt_id, base_url=base_url)
        if result["state"] in ("completed", "failed"):
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"prompt_id": prompt_id, "state": "timeout", "outputs": []}
        time.sleep(min(interval, remaining))
