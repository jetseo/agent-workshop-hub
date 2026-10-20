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
MAX_MODELS = 50
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
    result = {"base_url": entry["base_url"].rstrip("/"), "key_env": key_env,
              "data_policy": policy, "rpm": int(entry.get("rpm") or 0)}
    models = entry.get("models")
    if models is not None:
        if (not isinstance(models, list) or len(models) > MAX_MODELS
                or any(not isinstance(m, str) or not 1 <= len(m) <= 200 for m in models)):
            raise ValueError("models must be a list of up to " + str(MAX_MODELS) + " model names")
        result["models"] = models
    if entry.get("consented") is not None:
        result["consented"] = bool(entry["consented"])
    return result


KEY_PATTERN = re.compile(r"[\x21-\x7e]{8,512}")  # printable ASCII, no whitespace


def validate_key(key):
    key = (key or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("Token must be 8-512 printable characters without spaces")
    return key


def mask_key(key):
    return "\u2022\u2022\u2022\u2022" + key[-4:] if key and len(key) >= 8 else "\u2022\u2022\u2022\u2022"


class Providers:
    """Provider metadata lives in providers.json (no secrets). Tokens entered through the setup page
    live in a separate secrets.json next to it (mode 0600) and are only ever handed to chat()."""

    def __init__(self, path=None, secrets_path=None):
        self.path = Path(path) if path else default_path()
        self.secrets_path = Path(secrets_path) if secrets_path else self.path.with_name("secrets.json")

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
        previous = data.get(name, {})
        for keep in ("models", "consented"):        # re-registering must not forget these
            if keep not in entry and keep in previous:
                entry[keep] = previous[keep]
        data[name] = validate(name, entry)
        self._save(data)

    def add_model(self, name, model):
        """Remember a model the user has verified for this provider (newest first, no duplicates)."""
        data = self.load()
        if name not in data:
            raise ValueError("Unknown provider")
        models = [m for m in data[name].get("models", []) if m != model]
        data[name]["models"] = validate(name, {**data[name], "models": [model] + models[:MAX_MODELS - 1]})["models"]
        self._save(data)

    def set_consent(self, name, consented=True):
        data = self.load()
        if name not in data:
            raise ValueError("Unknown provider")
        data[name]["consented"] = bool(consented)
        self._save(data)

    def remove(self, name):
        data = self.load()
        if data.pop(name, None) is None:
            raise ValueError("Unknown provider")
        self._save(data)
        self.clear_key(name)

    # --- tokens ---------------------------------------------------------------------------
    def _load_keys(self):
        if not self.secrets_path.exists():
            return {}
        try:
            return json.loads(self.secrets_path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def _save_keys(self, keys):
        self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.secrets_path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(keys))
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.secrets_path)

    def set_key(self, name, key):
        if not NAME.fullmatch(name or ""):
            raise ValueError("Invalid provider name")
        keys = self._load_keys()
        keys[name] = validate_key(key)
        self._save_keys(keys)

    def clear_key(self, name):
        keys = self._load_keys()
        if keys.pop(name, None) is not None:
            self._save_keys(keys)

    def key_status(self, name):
        """Masked description for display; never the token itself."""
        key = self._load_keys().get(name)
        if key:
            return "saved " + mask_key(key)
        env = (self.load().get(name) or {}).get("key_env")
        if env:
            return "env " + env + (" (set)" if os.environ.get(env) else " (NOT SET)")
        return "none"

    def get(self, name):
        """Provider entry for use by chat(); includes api_key in memory only when one is stored."""
        entry = self.load().get(name)
        if entry is None:
            return None
        key = self._load_keys().get(name)
        return {**entry, "api_key": key} if key else entry
