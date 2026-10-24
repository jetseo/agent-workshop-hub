"""Notice new models at start-up and add the good free/local ones, so nobody has to go looking.

At start (and on request) JetAI asks each connected service for its current model list, compares it with the
list it saw last time, and registers at most a few NEW models that are
  * free or local (never one that may cost money),
  * chat models (no speech/image/embedding/preview), and
  * the best of the new ones for variety (modelpick.recommend).
The first look at a service only records what exists (no flood of additions). Everything added is listed in the
news the app shows once, and the whole thing can be switched off in settings. Nothing is downloaded: this only
registers names the service already offers. Files live next to providers.json: models-seen.json,
model-news.json, settings.json.
"""
import json
import os
import re
import threading
import time

from . import modelpick
from .usage import tier_for

SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}")      # what we are willing to store from a remote list
MAX_ADDED_PER_SERVICE = 2
MIN_SECONDS_BETWEEN_CHECKS = 600
MAX_NEWS = 20
DEFAULT_SETTINGS = {"auto_add_models": True, "engine_autostart": True, "code_guidance": False, "discovery_weekly": True,
                    "code_prefer_big": True, "code_auto_escalate": True}


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, path)


class ModelWatch:
    def __init__(self, providers, list_fn, *, info=None, home=None):
        self.providers, self.list_fn, self.info = providers, list_fn, info
        self.home = home or providers.path.parent
        self.seen_path, self.news_path = self.home / "models-seen.json", self.home / "model-news.json"
        self.settings_path = self.home / "settings.json"
        self._lock = threading.Lock()

    # ---- settings -------------------------------------------------------------------------------------------
    def settings(self):
        stored = _read(self.settings_path, {})
        return {**DEFAULT_SETTINGS, **{k: bool(v) for k, v in stored.items() if k in DEFAULT_SETTINGS}}

    def set_setting(self, key, value):
        if key not in DEFAULT_SETTINGS:
            raise ValueError("Unknown setting")
        data = {**_read(self.settings_path, {}), key: bool(value)}
        _write(self.settings_path, data)
        return self.settings()

    # ---- news -----------------------------------------------------------------------------------------------
    def news(self):
        return _read(self.news_path, [])

    def ack(self):
        _write(self.news_path, [])

    def _push(self, item):
        _write(self.news_path, (self.news() + [item])[-MAX_NEWS:])

    # ---- the check ------------------------------------------------------------------------------------------
    def check(self, *, force=False, now=None):
        """-> {"checked": n services, "added": {service: [models]}, "new": {service: count}, "skipped": reason|None}"""
        now = time.time() if now is None else now
        with self._lock:
            seen = _read(self.seen_path, {})
            last = float(seen.get("_checked_at", 0)) if isinstance(seen, dict) else 0.0
            if not force and now - last < MIN_SECONDS_BETWEEN_CHECKS:
                return {"checked": 0, "added": {}, "new": {}, "skipped": "recently checked"}
            auto = self.settings()["auto_add_models"]
            result = {"checked": 0, "added": {}, "new": {}, "skipped": None}
            for name, entry in self.providers.load().items():
                if entry["data_policy"] != "local" and not entry.get("consented"):
                    continue                                           # never talk to a service the user did not approve
                try:
                    listed = self.list_fn(self.providers.get(name))
                except Exception:                                      # offline, expired key, rate limit...: try next start
                    continue
                result["checked"] += 1
                names = [n for n in modelpick.chat_models(listed) if SAFE_NAME.fullmatch(n)]
                previous = seen.get(name)
                if previous is None:                                   # first look: remember, do not add
                    seen[name] = names
                    continue
                fresh = [n for n in names if n not in previous]
                if fresh:
                    result["new"][name] = len(fresh)
                free_fresh = [n for n in fresh if tier_for(entry, n) in ("local", "free") and n not in entry.get("models", [])]
                picks = modelpick.recommend(free_fresh, MAX_ADDED_PER_SERVICE) if (auto and free_fresh) else []
                for model in picks:
                    self.providers.add_model(name, model)
                if picks:
                    result["added"][name] = picks
                    self._push({"ts": now, "service": name, "added": picks,
                                "about": [self._about(m) for m in picks]})
                seen[name] = sorted(set(previous) | set(names))
            seen["_checked_at"] = now
            _write(self.seen_path, seen)
            return result

    def _about(self, model):
        described = self.info.describe(model) if self.info else None
        return {"model": model, "tags": described["tags"] if described else [], "desc": described["desc"] if described else ""}

    def start_in_background(self, refresh_info=True):
        """Non-blocking: refresh the public model catalog (once a day), then look for new models."""
        def work():
            try:
                if refresh_info and self.info is not None:
                    self.info.refresh()
                self.check()
            except Exception:
                pass                                                   # a background courtesy must never disturb the app
        thread = threading.Thread(target=work, name="modelwatch", daemon=True)
        thread.start()
        return thread
