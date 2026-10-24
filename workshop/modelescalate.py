"""Helpers for "use the 9B for code work": make sure the built-in engine serves it, and free the GPU first.

Measured (docs/agent-eval.md, 2026-10-24): with the direct mode the Qwen3.5 9B solves clearly more code tasks than the 7B
(multi-file tasks 19/20 vs 12/20, older tasks 28/30 vs 59/90) but is 6-8x slower and fills almost the whole 12 GB GPU. So it is a
chosen model, not a guess: only this one id, only when it is already installed (nothing is downloaded here), only for the
user-approved cases (new direct-mode runs by default; one automatic retry after a failure). Both are settings the user can switch off.
"""
import json
import time
import urllib.request

BIG_ID = "qwen3.5-9b"
BIG_REF = "jetai-engine/" + BIG_ID
OLLAMA = "http://127.0.0.1:11434"


def installed(status):
    """True when the engine status lists the 9B as installed (file present)."""
    return any(m.get("id") == BIG_ID and m.get("installed") for m in (status or {}).get("models", []))


def serving(status):
    return bool(status and status.get("running") and status.get("ready") and status.get("active") == BIG_ID)


def unload_ollama(opener=None, timeout=5):
    """Ask a local Ollama to drop the models it holds in GPU memory (keep_alive 0), so the 9B can use the GPU. Best effort: returns how many were asked."""
    opener = opener or urllib.request.build_opener()
    asked = 0
    try:
        with opener.open(urllib.request.Request(OLLAMA + "/api/ps"), timeout=timeout) as reply:
            loaded = [m.get("name") for m in json.loads(reply.read(200_000).decode("utf-8")).get("models", []) if m.get("name")]
        for name in loaded[:10]:
            body = json.dumps({"model": name, "keep_alive": 0}).encode()
            request = urllib.request.Request(OLLAMA + "/api/generate", data=body, headers={"Content-Type": "application/json"}, method="POST")
            with opener.open(request, timeout=timeout) as reply:
                reply.read(1000)
            asked += 1
    except Exception:
        pass                                                      # Ollama not installed or not running: nothing to free
    return asked


def ensure_serving(engine, unload=unload_ollama, wait=150, sleep=time.sleep, clock=time.time):
    """Start the 9B on the built-in engine if it is installed and not already serving. True when it is ready to answer."""
    status = engine.status()
    if serving(status):
        return True
    if not installed(status):
        return False
    unload()
    engine.start(BIG_ID)
    deadline = clock() + wait
    while clock() < deadline:
        if serving(engine.status()):
            return True
        sleep(2)
    return False
