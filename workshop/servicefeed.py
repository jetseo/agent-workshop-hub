"""Daily list of NEW free AI services, so the wizard is not limited to the ones built into JetAI.

Source: a public, community-kept list on GitHub (awesome-free-llm-apis, "permanently free" providers). The app
reads it through GitHub's API, pulls each provider's name, sign-up page, free-limit text and OpenAI-compatible
address, drops anything unsafe (not https, localhost/private addresses, credentials in the URL, duplicates of
built-in services), and keeps only services whose address still answers an anonymous `GET <address>/models`
(401/403/200). What is left shows up in the wizard under "새로 발견된 서비스 (미검증)".

Trust rules: entries from the internet are never trusted as "free": the user must tick that THEIR key has no
payment method attached before the models count as free (otherwise only `-free`/`:free` names do). Nothing is
sent anywhere except the anonymous reads above. Refresh runs at most every few hours (or on request).
"""
import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from .catalog_check import probe

SOURCE_URL = "https://api.github.com/repos/amardeeplakshkar/awesome-free-llm-apis/readme"
SOURCE_LABEL = "github.com/amardeeplakshkar/awesome-free-llm-apis"
MIN_SECONDS_BETWEEN_REFRESH = 6 * 3600
NEW_FOR_SECONDS = 14 * 86400
MAX_ENTRIES = 60
MAX_BYTES = 2_000_000
ALIVE = (200, 401, 403)
HEADING = re.compile(r"^[ \t]*#{2,4}\s*\[([^\]]+)\]\((https?://[^)\s]+)\)", re.M)      # the real list indents its headings
ROW = r"\*\*%s\*\*\s*\|\s*(.+?)\s*\|"


def default_path():
    return Path.home() / ".agent-workshop" / "service-feed.json"


def _fetch(url=SOURCE_URL, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "jetai-servicefeed", "Accept": "application/vnd.github.raw"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(MAX_BYTES).decode("utf-8", "replace")


def safe_https_url(url):
    """True for a public https address without credentials (no localhost, no private/loopback IPs)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or "." not in host or host.endswith((".local", ".internal", ".lan")):
        return False
    try:
        address = ipaddress.ip_address(host)
        return not (address.is_private or address.is_loopback or address.is_link_local or address.is_reserved)
    except ValueError:
        return True


def slug(name):
    base = re.sub(r"[^a-z0-9]+", "-", re.sub(r"[^\x00-\x7f]", "", name).lower()).strip("-")
    return (base or "service")[:30]


def parse_readme(text):
    """-> list of {name, key_url, base_url, limits, models}. Only providers that state an OpenAI-compatible address."""
    out = []
    matches = list(HEADING.finditer(text))
    for i, match in enumerate(matches):
        block = text[match.end(): matches[i + 1].start() if i + 1 < len(matches) else len(text)]
        compat = re.search(ROW % "OpenAI Compat", block)
        address = re.search(r"`(https://[^`\s]+)`", compat.group(1)) if compat else None
        if not address:
            continue
        limits = re.search(ROW % "Rate Limits", block)
        models = re.search(ROW % "Free Models", block)
        out.append({"name": re.sub(r"[^\x20-\x7e]", "", match.group(1)).strip()[:60] or "service", "key_url": match.group(2),
                    "base_url": address.group(1).rstrip("/").rstrip("`"),
                    "limits": (limits.group(1) if limits else "")[:120], "models": (models.group(1) if models else "")[:160]})
    return out


class ServiceFeed:
    def __init__(self, path=None, fetch=_fetch, prober=probe, builtin=()):
        self.path = Path(path) if path else default_path()
        self.fetch, self.prober = fetch, prober
        self._builtin = list(builtin)                 # catalog entries to avoid duplicating: dicts with id/base_url
        self.data = self._read()

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data.get("entries"), list):
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {"fetched_at": 0.0, "entries": []}

    def set_builtin(self, builtin):
        self._builtin = list(builtin)

    def _duplicate(self, base_url, sid):
        key = urlparse(base_url)
        for known in self._builtin:
            other = urlparse(known["base_url"])
            if known["id"] == sid or (other.hostname == key.hostname and other.path.rstrip("/") == key.path.rstrip("/")):
                return True
        return False

    def refresh(self, force=False, now=None):
        """-> {"added": [labels], "total": n, "skipped": reason|None}. Throttled; failures keep the previous list."""
        now = time.time() if now is None else now
        if not force and now - self.data.get("fetched_at", 0) < MIN_SECONDS_BETWEEN_REFRESH:
            return {"added": [], "total": len(self.data["entries"]), "skipped": "recently refreshed"}
        try:
            parsed = parse_readme(self.fetch())
        except (OSError, ValueError, urllib.error.URLError):
            return {"added": [], "total": len(self.data["entries"]), "skipped": "offline"}
        known = {e["id"]: e for e in self.data["entries"]}
        entries, added = [], []
        for item in parsed[:MAX_ENTRIES]:
            sid = slug(item["name"])
            if not safe_https_url(item["base_url"]) or not safe_https_url(item["key_url"]) or self._duplicate(item["base_url"], sid):
                continue
            status, _label = self.prober(item["base_url"])
            if status not in ALIVE:
                continue                                               # dead or moved: do not offer it
            old = known.get(sid)
            entry = {"id": sid, "label": item["name"], "base_url": item["base_url"], "key_url": item["key_url"],
                     "limits": item["limits"] or "공개 목록 참고", "models": item["models"],
                     "discovered_at": old["discovered_at"] if old else now, "probe": status}
            entries.append(entry)
            if not old:
                added.append(entry["label"])
        self.data = {"fetched_at": now, "source": SOURCE_LABEL, "entries": entries}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)
        return {"added": added, "total": len(entries), "skipped": None}

    def catalog_entries(self, now=None):
        """Wizard-shaped entries (same keys as setup_page.CATALOG, plus discovered/new). Never trusted as free."""
        now = time.time() if now is None else now
        out = []
        for e in self.data["entries"]:
            out.append({"id": e["id"], "label": e["label"] + " (새로 발견·미검증)", "base_url": e["base_url"], "policy": "cloud-may-train",
                        "key_url": e["key_url"], "model": "", "needs_key": True, "group": "new", "free": "suffix", "free_match": [],
                        "limits": e["limits"],
                        "note": "공개 목록에서 자동으로 찾은 서비스입니다(출처: " + SOURCE_LABEL + "). 주소가 응답하는 것만 확인했고 무료 여부·한도는 "
                                "확인하지 못했습니다. 결제 수단을 연결하지 않은 키라면 아래 확인 칸을 체크해 무료로 쓰세요.",
                        "discovered": True, "is_new": now - e["discovered_at"] < NEW_FOR_SECONDS})
        return out
