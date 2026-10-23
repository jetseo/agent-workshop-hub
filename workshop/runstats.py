"""Per-model run outcomes (success, failure, seconds) so the router can skip models that keep failing.

Append-only JSONL beside the usage ledger. Holds no prompts or outputs. A broken or full disk must never
fail a step, so callers treat OSError as "no stats".
"""
import json
import os
import threading
import time
from pathlib import Path

MAX_READ_BYTES = 2_000_000
RECENT_WINDOW_DAYS = 30


def default_path():
    return Path.home() / ".agent-workshop" / "runstats.jsonl"


class RunStats:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()
        self._lock = threading.Lock()

    def record(self, model, *, ok, seconds, kind="text", when=None, tokens=None):
        extra = {"tokens": int(tokens)} if isinstance(tokens, (int, float)) else {}
        line = json.dumps({**extra, "ts": when if when is not None else time.time(), "model": str(model),
                           "ok": bool(ok), "seconds": round(max(0.0, float(seconds)), 2), "kind": kind})
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def _records(self):
        if not self.path.exists():
            return []
        with open(self.path, "rb") as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_READ_BYTES:
                handle.seek(size - MAX_READ_BYTES)
                handle.readline()
            data = handle.read().decode("utf-8", "replace")
        out = []
        for raw in data.splitlines():
            try:
                record = json.loads(raw)
                if isinstance(record.get("model"), str) and isinstance(record.get("ts"), (int, float)):
                    out.append(record)
            except ValueError:
                continue
        return out

    def load(self, days=RECENT_WINDOW_DAYS, now=None, kind=None):
        """{model: {"runs", "failures", "seconds", "streak_fail", "last_ts"}} for the recent window; matches
        router.route(stats=). streak_fail counts the failures at the very end of the history (0 after a success)."""
        cutoff = (time.time() if now is None else now) - days * 86400
        stats = {}
        for r in sorted(self._records(), key=lambda rec: rec["ts"]):
            if r["ts"] < cutoff:
                continue
            is_code = str(r.get("kind", "text")).startswith("code:")
            if is_code != (kind == "code"):
                continue                                       # default view = chat routing only; code runs via kind="code"
            entry = stats.setdefault(r["model"], {"runs": 0, "failures": 0, "seconds": 0.0, "streak_fail": 0, "last_ts": 0.0})
            entry["runs"] += 1
            entry["failures"] += 0 if r.get("ok") else 1
            entry["streak_fail"] = 0 if r.get("ok") else entry["streak_fail"] + 1
            entry["last_ts"] = r["ts"]
            entry["seconds"] = round(entry["seconds"] + float(r.get("seconds", 0)), 2)
        return stats
