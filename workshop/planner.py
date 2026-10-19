"""Local-first, read-only task planning. No commands or cloud APIs are executed."""
import json
import urllib.request
from urllib.parse import urlparse

SYSTEM = """Break a software request into 1-4 independent tasks. Return ONLY a JSON
object: {"tasks":[{"title":"...","kind":"code|qa|plan","depends_on":[]}]}
Dependencies are zero-based indices of earlier tasks. No shell commands.
Keep tasks narrowly scoped, avoid overlapping file edits. Never invent requirements."""

def validate_plan(data):
    if not isinstance(data, dict) or not isinstance(data.get("tasks"), list):
        raise ValueError("Expected object with tasks list")
    tasks = data["tasks"]
    if not 1 <= len(tasks) <= 4:
        raise ValueError("Plan must have 1-4 tasks")
    for index, task in enumerate(tasks):
        if not isinstance(task, dict) or set(task) != {"title", "kind", "depends_on"}:
            raise ValueError("Invalid task fields")
        if not isinstance(task["title"], str) or not 1 <= len(task["title"].strip()) <= 300:
            raise ValueError("Invalid task title")
        if task["kind"] not in ("code", "qa", "plan"):
            raise ValueError("Invalid task kind")
        deps = task["depends_on"]
        if not isinstance(deps, list) or any(type(d) is not int or d < 0 or d >= index for d in deps) or len(set(deps)) != len(deps):
            raise ValueError("Invalid dependencies")
    return data

def plan_local(request, *, model, base_url="http://127.0.0.1:11434", timeout=90):
    if not isinstance(request, str) or not 1 <= len(request.strip()) <= 4000:
        raise ValueError("Request must be 1-4000 characters")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("An installed Ollama model is required")
    parsed = urlparse(base_url)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or parsed.port not in (None, 11434) or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Only loopback Ollama URL is allowed")
    payload = json.dumps({"model": model, "stream": False, "format": "json",
                          "messages": [{"role": "system", "content": SYSTEM},
                                       {"role": "user", "content": request}]}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as response:
        content = json.load(response)["message"]["content"]
    return validate_plan(json.loads(content))
