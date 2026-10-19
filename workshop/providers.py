"""Provider registry. Stores connection metadata only, never API keys.

A key is referenced by the NAME of an environment variable (key_env) that the user sets.
"""
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

POLICIES = ("local", "cloud-no-train", "cloud-may-train", "unknown")
NAME = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def default_path():
    return Path.home() / ".agent-workshop" / "providers.json"


def validate(name, entry):
    if not NAME.fullmatch(name or ""):
        raise ValueError("Provider name must be lowercase letters, digits, - or _")
    url = urlparse(entry.get("base_url", ""))
    loopback = url.hostname in ("127.0.0.1", "localhost")
    if url.username or url.password or not url.hostname:
        raise ValueError("base_url must not contain credentials")
    if not (url.scheme == "https" or (url.scheme == "http" and loopback)):
        raise ValueError("base_url must be https (http only for loopback)")
    policy = entry.get("data_policy", "unknown")
    if policy not in POLICIES:
        raise ValueError("data_policy must be one of " + ", ".join(POLICIES))
    if policy == "local" and not loopback:
        raise ValueError("A non-loopback provider cannot be marked local")
    key_env = entry.get("key_env")
    if key_env is not None and not ENV.fullmatch(key_env):
        raise ValueError("key_env must be an environment variable NAME, not a key")
    return {"base_url": entry["base_url"].rstrip("/"), "key_env": key_env,
            "data_policy": policy, "rpm": int(entry.get("rpm") or 0)}


class Providers:
    def __init__(self, path=None):
        self.path = Path(path) if path else default_path()

    def load(self):
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8")).get("providers", {})

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"providers": data}, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)

    def add(self, name, **entry):
        data = self.load()
        data[name] = validate(name, entry)
        self._save(data)

    def remove(self, name):
        data = self.load()
        if data.pop(name, None) is None:
            raise ValueError("Unknown provider")
        self._save(data)

    def get(self, name):
        return self.load().get(name)
