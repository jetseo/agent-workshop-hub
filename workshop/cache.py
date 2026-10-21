"""Opt-in cache of text replies, so an identical request is answered without calling a model again.

Only side-effect-free text replies are cached, keyed by sha256(model + prompt). Entries expire (TTL), the
store is capped, files are private (0600), and every hit books the tokens it saved in a small JSONL so the
saving can be measured. Cached replies contain prompt-derived content: keep the folder out of version control.
A broken or full disk must never fail a step, so callers treat OSError as a miss.
"""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

from .usage import estimate_tokens

DEFAULT_TTL_DAYS = 7
MAX_ENTRIES = 500
MAX_REPLY_CHARS = 200_000


def default_folder():
    return Path.home() / ".agent-workshop" / "cache"


class ResultCache:
    def __init__(self, folder=None, *, ttl_days=DEFAULT_TTL_DAYS, max_entries=MAX_ENTRIES):
        self.folder = Path(folder) if folder else default_folder()
        self.ttl = ttl_days * 86400
        self.max_entries = max_entries
        self._lock = threading.Lock()

    @staticmethod
    def key(model, prompt):
        return hashlib.sha256((model + "\0" + prompt).encode("utf-8")).hexdigest()

    def _path(self, key):
        return self.folder / (key + ".json")

    def get(self, model, prompt, now=None):
        """Cached reply or None. A hit books the estimated tokens it saved."""
        path = self._path(self.key(model, prompt))
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        now = time.time() if now is None else now
        if (entry.get("model") != model or not isinstance(entry.get("reply"), str)
                or now - float(entry.get("ts", 0)) > self.ttl):
            return None
        self._book_hit(model, prompt, entry["reply"], now)
        return entry["reply"]

    def put(self, model, prompt, reply, now=None):
        if not isinstance(reply, str) or not reply.strip() or len(reply) > MAX_REPLY_CHARS:
            return
        with self._lock:
            self.folder.mkdir(parents=True, exist_ok=True)
            path = self._path(self.key(model, prompt))
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"model": model, "ts": time.time() if now is None else now, "reply": reply}, handle,
                          ensure_ascii=False)
            self._prune()

    def _prune(self):
        files = sorted(self.folder.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for old in files[:max(0, len(files) - self.max_entries)]:
            try:
                old.unlink()
            except OSError:
                pass

    def _book_hit(self, model, prompt, reply, now):
        line = json.dumps({"ts": now, "model": model, "saved_prompt": estimate_tokens(prompt),
                           "saved_completion": estimate_tokens(reply)})
        try:
            with self._lock:
                self.folder.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.folder / "hits.jsonl", os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except OSError:
            pass

    def savings(self, days=30, now=None):
        """{"hits", "tokens_saved"} over the window (estimates, like all board token counts)."""
        cutoff = (time.time() if now is None else now) - days * 86400
        hits = saved = 0
        try:
            lines = (self.folder / "hits.jsonl").read_text(encoding="utf-8").splitlines()
        except OSError:
            return {"hits": 0, "tokens_saved": 0}
        for raw in lines:
            try:
                r = json.loads(raw)
                if r["ts"] >= cutoff:
                    hits += 1
                    saved += int(r["saved_prompt"]) + int(r["saved_completion"])
            except (ValueError, KeyError, TypeError):
                continue
        return {"hits": hits, "tokens_saved": saved}
