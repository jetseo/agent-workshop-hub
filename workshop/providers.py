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
PRIMARY_LABEL = "기본"
ENV = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")


def default_path():
    return Path.home() / ".agent-workshop" / "providers.json"


def validate(name, entry):
    if not NAME.fullmatch(name or ""):
        raise ValueError("서비스 이름은 영문 소문자·숫자·-·_ 만 쓸 수 있습니다.")
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
        raise ValueError("이 PC(127.0.0.1)가 아닌 서비스는 로컬로 표시할 수 없습니다.")
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
    if entry.get("guard"):
        if not isinstance(entry["guard"], str) or not re.fullmatch(r"[a-z0-9_-]{1,30}", entry["guard"]):
            raise ValueError("guard must be a short id")
        result["guard"] = entry["guard"]      # free use is gated by workshop/quotaguard.py (fail-closed), never assumed
    if entry.get("free_all"):
        result["free_all"] = True            # every chat model of this service is free within its free-tier limits
    match = entry.get("free_match")
    if match:
        if (not isinstance(match, list) or len(match) > 5
                or any(not isinstance(m, str) or not 1 <= len(m) <= 40 for m in match)):
            raise ValueError("free_match must be a short list of name fragments")
        result["free_match"] = [m.lower() for m in match]
    accounts = entry.get("accounts")
    if accounts:
        if (not isinstance(accounts, list) or len(accounts) >= MAX_ACCOUNTS
                or any(not isinstance(a, dict) or not LABEL.fullmatch(str(a.get("label", ""))) for a in accounts)):
            raise ValueError("accounts must be a short list of labelled entries")
        result["accounts"] = [{"label": a["label"], "rpm": int(a.get("rpm") or 0)} for a in accounts]
    return result


LABEL = re.compile(r"[A-Za-z0-9가-힣_-]{1,20}")
MAX_ACCOUNTS = 10


KEY_PATTERN = re.compile(r"[\x21-\x7e]{8,512}")  # printable ASCII, no whitespace


def validate_key(key):
    key = (key or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("토큰은 공백 없는 8~512자여야 합니다. 앞뒤 공백이나 줄바꿈이 섞이지 않았는지 확인하세요.")
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
        for keep in ("models", "consented", "accounts", "free_all", "free_match", "guard"):        # re-registering must not forget these
            if keep not in entry and keep in previous:
                entry[keep] = previous[keep]
        data[name] = validate(name, entry)
        self._save(data)

    def add_model(self, name, model):
        """Remember a model the user has verified for this provider (newest first, no duplicates)."""
        data = self.load()
        if name not in data:
            raise ValueError("등록되지 않은 서비스입니다.")
        models = [m for m in data[name].get("models", []) if m != model]
        data[name]["models"] = validate(name, {**data[name], "models": [model] + models[:MAX_MODELS - 1]})["models"]
        self._save(data)

    def set_consent(self, name, consented=True):
        data = self.load()
        if name not in data:
            raise ValueError("등록되지 않은 서비스입니다.")
        data[name]["consented"] = bool(consented)
        self._save(data)

    def remove(self, name):
        data = self.load()
        entry = data.pop(name, None)
        if entry is None:
            raise ValueError("등록되지 않은 서비스입니다.")
        self._save(data)
        self.clear_key(name)
        for account in entry.get("accounts", []):
            self.clear_key(name + "#" + account["label"])

    # --- extra accounts (keys the user owns, used in turn; the first key is the "기본" account) ---
    def add_account(self, name, label, key, rpm=0):
        data = self.load()
        if name not in data:
            raise ValueError("등록되지 않은 서비스입니다.")
        if not LABEL.fullmatch(label or ""):
            raise ValueError("계정 별칭은 1~20자의 영문·숫자·한글·-·_ 만 쓸 수 있습니다.")
        if label == PRIMARY_LABEL:
            raise ValueError("'" + PRIMARY_LABEL + "' is the first key's name; choose another label")
        accounts = [a for a in data[name].get("accounts", []) if a["label"] != label]
        if len(accounts) >= MAX_ACCOUNTS - 1:
            raise ValueError("서비스당 계정은 최대 " + str(MAX_ACCOUNTS) + "개까지 등록할 수 있습니다.")
        rpm = int(rpm or 0)
        if rpm < 0 or rpm > 100000:
            raise ValueError("rpm must be 0 (unlimited) to 100000")
        key = validate_key(key)
        keys = self._load_keys()
        keys[name + "#" + label] = key
        self._save_keys(keys)
        data[name]["accounts"] = accounts + [{"label": label, "rpm": rpm}]
        self._save(data)

    def remove_account(self, name, label):
        data = self.load()
        accounts = (data.get(name) or {}).get("accounts", [])
        if not any(a["label"] == label for a in accounts):
            raise ValueError("등록되지 않은 계정입니다.")
        data[name]["accounts"] = [a for a in accounts if a["label"] != label]
        self._save(data)
        self.clear_key(name + "#" + label)

    def list_accounts(self, name):
        """Display view: masked tokens only."""
        entry = self.load().get(name) or {}
        keys = self._load_keys()
        out = []
        if keys.get(name):
            out.append({"label": PRIMARY_LABEL, "token": mask_key(keys[name]), "rpm": entry.get("rpm", 0), "primary": True})
        for account in entry.get("accounts", []):
            key = keys.get(name + "#" + account["label"])
            out.append({"label": account["label"], "token": mask_key(key) if key else "none", "rpm": account.get("rpm", 0), "primary": False})
        return out

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
            raise ValueError("서비스 이름이 올바르지 않습니다.")
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
        keys = self._load_keys()
        key = keys.get(name)
        result = {**entry, "api_key": key} if key else dict(entry)
        extra = [{"label": a["label"], "api_key": keys[name + "#" + a["label"]], "rpm": a.get("rpm", 0)}
                 for a in entry.get("accounts", []) if keys.get(name + "#" + a["label"])]
        if extra:
            first = [{"label": PRIMARY_LABEL, "api_key": key, "rpm": entry.get("rpm", 0)}] if key else []
            result["accounts"] = first + extra          # in memory only; chat() rotates through these
        else:
            result.pop("accounts", None)
        return result
