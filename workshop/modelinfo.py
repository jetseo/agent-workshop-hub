"""What is this model, and what is it good at? Looked up in OpenRouter's public model catalog.

https://openrouter.ai/api/v1/models needs no key and describes ~450 models: context length, reasoning support,
image input, price, a short description and (for many) Artificial Analysis benchmark scores. JetAI downloads it
at most once a day, keeps a compact copy in ~/.agent-workshop/modelinfo.json, and matches your connected models
to it by a normalised name. A match gives evidence (benchmark rank, reasoning flag, context size) instead of a
guess from the name; no match falls back to the name heuristics in modelpick. Nothing about you or your prompts
is sent: it is one anonymous GET. A failed download keeps the old copy.
"""
import json
import os
import re
import time
import urllib.request
from pathlib import Path

CATALOG_URL = "https://openrouter.ai/api/v1/models"
TTL_SECONDS = 24 * 3600
MAX_BYTES = 8_000_000
NOISE = re.compile(r"(instruct|versatile|chat|it|preview|latest|turbo|free|exp|beta|\d{4}-?\d{2}-?\d{2}|\d{6,8})")


def default_path():
    return Path.home() / ".agent-workshop" / "modelinfo.json"


def normalise(name):
    """'models/gemini-2.5-pro' / 'google/gemini-2.5-pro:free' / 'llama-3.3-70b-versatile' -> comparable key."""
    base = str(name).lower().split(":")[0].split("/")[-1]
    parts = [p for p in re.split(r"[-_ ]", base) if p]
    while len(parts) > 1 and NOISE.fullmatch(parts[-1]):
        parts.pop()
    return re.sub(r"[^a-z0-9]", "", "".join(parts))


def _compact(item):
    pricing = item.get("pricing") or {}
    bench = (item.get("benchmarks") or {}).get("artificial_analysis") or {}
    arch = item.get("architecture") or {}
    priced = [str(pricing[k]) for k in ("prompt", "completion") if k in pricing]
    free = str(item.get("id", "")).endswith(":free") or (bool(priced) and all(p in ("0", "0.0") for p in priced))
    return {"id": item.get("id"), "ctx": item.get("context_length") or 0,
            "reasoning": bool(item.get("reasoning")), "vision": "image" in (arch.get("input_modalities") or []),
            "iq": bench.get("intelligence_index"), "coding": bench.get("coding_index"),
            "desc": re.sub(r"\s+", " ", str(item.get("description") or ""))[:200], "free": free,
            "hf": item.get("hugging_face_id") or ""}


def _percentile_cut(values, fraction):
    values = sorted(v for v in values if isinstance(v, (int, float)))
    return values[int(len(values) * fraction)] if values else None


def _download(url=CATALOG_URL, timeout=30):
    request = urllib.request.Request(url, headers={"User-Agent": "jetai-modelinfo", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read(MAX_BYTES).decode("utf-8"))


class ModelInfo:
    def __init__(self, path=None, fetch=_download, ttl=TTL_SECONDS):
        self.path, self.fetch, self.ttl = Path(path) if path else default_path(), fetch, ttl
        self.records, self.fetched_at, self._index, self._cuts = [], 0.0, {}, {}
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self.records, self.fetched_at = data["records"], float(data["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            self.records, self.fetched_at = [], 0.0
        self._build()

    def _build(self):
        self._index = {}
        for rec in self.records:
            self._index.setdefault(normalise(rec["id"]), rec)
        self._cuts = {key: (_percentile_cut([r.get(key) for r in self.records], 0.3),
                            _percentile_cut([r.get(key) for r in self.records], 0.7)) for key in ("iq", "coding")}

    def stale(self, now=None):
        return (time.time() if now is None else now) - self.fetched_at > self.ttl

    def refresh(self, force=False, now=None):
        """Download the catalog when it is older than a day. Returns True when new data was stored."""
        if not force and self.records and not self.stale(now):
            return False
        try:
            data = self.fetch()
            records = [_compact(item) for item in data.get("data", []) if isinstance(item, dict) and item.get("id")]
            if len(records) < 20:
                return False                               # an error page or an empty list: keep what we have
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        self.records, self.fetched_at = records, time.time() if now is None else now
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"fetched_at": self.fetched_at, "records": self.records}, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)
        self._build()
        return True

    def lookup(self, name):
        return self._index.get(normalise(name)) if name else None

    def level(self, rec):
        """1 light / 2 standard / 3 strong from the benchmark percentile, or None without benchmark data."""
        low, high = self._cuts.get("iq", (None, None))
        iq = rec.get("iq")
        if iq is None or low is None:
            return None
        return 1 if iq < low else 3 if iq >= high else 2

    def strong_at_code(self, rec):
        _low, high = self._cuts.get("coding", (None, None))
        return rec.get("coding") is not None and high is not None and rec["coding"] >= high

    def facts(self, name):
        """Evidence for the router: {"level", "code", "reasoning", "ctx", "vision"} or None when unknown."""
        rec = self.lookup(name)
        if not rec:
            return None
        return {"level": self.level(rec), "code": self.strong_at_code(rec), "reasoning": rec["reasoning"],
                "ctx": rec["ctx"], "vision": rec["vision"]}

    def describe(self, name):
        """{"tags", "ctx", "desc", "level", "source"} for the screen, or None when the catalog does not know it."""
        rec = self.lookup(name)
        if not rec:
            return None
        tags, level = [], self.level(rec)
        if level:
            tags.append({1: "벤치마크 하위", 2: "벤치마크 중간", 3: "벤치마크 상위"}[level])
        if self.strong_at_code(rec) or re.search(r"\bcod(?:e|er|ing)\b", rec["desc"], re.I):
            tags.append("코드")
        if rec["reasoning"]:
            tags.append("추론")
        if rec["ctx"] >= 200_000:
            tags.append("긴 문서(%s)" % (("%.0fM" % (rec["ctx"] / 1_000_000)) if rec["ctx"] >= 900_000 else ("%dK" % (rec["ctx"] // 1000))))
        if rec["vision"]:
            tags.append("이미지 입력")
        return {"tags": tags, "ctx": rec["ctx"], "desc": rec["desc"], "level": level, "source": "OpenRouter 공개 목록"}


def install(info):
    """Let modelpick.capability() use the catalog as evidence (None removes it)."""
    from . import modelpick
    modelpick.INFO_LOOKUP = info.facts if info else None
