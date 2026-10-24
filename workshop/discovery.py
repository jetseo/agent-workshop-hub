"""Discovery inbox (step D0): cards that RECOMMEND a new model, service or piece of news. Nothing here installs anything.

This step only stores and shows cards: it makes no network request. Later steps (see docs/discovery-helper-plan.md) fill
the inbox from allow-listed public sources; adding a card's model or service always goes through the existing
confirmation flows (exact download size, service wizard). Off by default.

A card is plain data validated here: the source link must be https on an allow-listed host, every text is length-limited,
and the page text a card came from is never stored as an instruction. Dismissed ids are remembered so a card does not
come back.
"""
import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

KINDS = ("model", "service", "news")
SOURCE_HOSTS = ("huggingface.co", "github.com", "api.github.com")
MAX_CARDS = 200
MAX_DISMISSED = 1000


class DiscoveryError(Exception):
    pass


def _clip(value, limit):
    return " ".join(str(value or "").split())[:limit]


def _read(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def validate_card(card):
    """-> a clean copy of the card, or raises DiscoveryError."""
    if not isinstance(card, dict):
        raise DiscoveryError("카드 형식이 올바르지 않습니다.")
    kind = card.get("kind")
    if kind not in KINDS:
        raise DiscoveryError("알 수 없는 카드 종류입니다.")
    card_id = _clip(card.get("id"), 120)
    title = _clip(card.get("title"), 120)
    if not card_id or not title:
        raise DiscoveryError("카드에는 id와 제목이 필요합니다.")
    url = str(card.get("source_url") or "")
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not any(host == h or host.endswith("." + h) for h in SOURCE_HOSTS) or parsed.username or parsed.password or len(url) > 300:
        raise DiscoveryError("출처 주소가 허용되지 않습니다(허용된 사이트의 https 주소만).")
    facts = card.get("facts") if isinstance(card.get("facts"), dict) else {}
    clean_facts = {_clip(k, 40): _clip(v, 120) for k, v in list(facts.items())[:12] if _clip(k, 40)}
    fit = card.get("fit") if card.get("fit") in ("gpu", "partial", "cpu", "no") else None
    return {"id": card_id, "kind": kind, "title": title, "summary": _clip(card.get("summary"), 400), "source_url": url,
            "facts": clean_facts, "fit": fit, "measured": card.get("measured") is True, "ai_summary": card.get("ai_summary") is True}


class Discovery:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self._lock = threading.Lock()

    def _load(self):
        data = _read(self.path)
        return {"enabled": data.get("enabled") is True, "cards": [c for c in data.get("cards", []) if isinstance(c, dict)],
                "dismissed": [str(x) for x in data.get("dismissed", [])][-MAX_DISMISSED:], "last_run": data.get("last_run")}

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    def state(self):
        with self._lock:
            data = self._load()
        data["unread"] = sum(1 for c in data["cards"] if c.get("status") == "new")
        return data

    def set_enabled(self, enabled):
        with self._lock:
            data = self._load()
            data["enabled"] = enabled is True
            self._save(data)

    def add_card(self, card):
        """Store a validated card unless it is already there or was dismissed. Returns True when it was added."""
        clean = validate_card(card)
        with self._lock:
            data = self._load()
            if clean["id"] in data["dismissed"] or any(c.get("id") == clean["id"] for c in data["cards"]):
                return False
            clean.update({"status": "new", "created": self.clock()})
            data["cards"] = ([clean] + data["cards"])[:MAX_CARDS]
            self._save(data)
            return True

    def dismiss(self, card_id):
        with self._lock:
            data = self._load()
            data["cards"] = [c for c in data["cards"] if c.get("id") != card_id]
            if card_id not in data["dismissed"]:
                data["dismissed"].append(str(card_id)[:120])
            self._save(data)

    def mark_read(self):
        with self._lock:
            data = self._load()
            for card in data["cards"]:
                card["status"] = "seen"
            self._save(data)
