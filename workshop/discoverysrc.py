"""Discovery source D1: new GGUF model candidates from Hugging Face, turned into recommendation cards (never installed here).

Only anonymous, read-only requests to huggingface.co (the same host the built-in engine already downloads from), for two
quantiser accounts. Every value on a card comes from the API's own fields (size, license tag, downloads, date) or from
JetAI's memory estimate for this PC; no performance claim is made ("성능은 측정되지 않았습니다"). Text from the internet is
never given to a model and never becomes a button or a command: the card only links to the model page.

Filtering is rule-based: 3-35 B parameters in the name, a chat-capable name, updated recently, not already known to the
engine. Whether a card can be added is decided elsewhere (the engine's exact-size confirmation flow).
"""
import re
import time
import urllib.parse
import urllib.request

from . import engine, modelpick

AUTHORS = ("unsloth", "bartowski")
LIST_URL = "https://huggingface.co/api/models?author=%s&filter=gguf&sort=lastModified&direction=-1&limit=40"
RECENT_DAYS = 120
MIN_B, MAX_B = 3.0, 35.0
MAX_CARDS = 6
WANTED_FILE = re.compile(r"Q4_K_M\.gguf$")
SPLIT_FILE = re.compile(r"-\d{5}-of-\d{5}\.gguf$")
# Not text-chat models even though they are GGUF (seen in the first real answer: a video model, vision/omni models): never recommended as a code model.
NOT_TEXT = re.compile(r"wan\d|video|t2v|i2v|ti2v|-vl|vl-|vl|omni|vision|diffusion|asr|mmproj|ocr", re.I)


def _age_days(stamp, now):
    try:
        import datetime
        when = datetime.datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None
    return (now - when) / 86400


def known_repos():
    return {m["repo"] for m in engine.MODELS} | {c["repo"] for c in engine.CANDIDATES}


def candidates(hardware, opener=None, now=None, known=None, authors=AUTHORS):
    """-> list of card dicts (validated later by `discovery.validate_card`). Network errors for one account are skipped."""
    opener = opener or urllib.request.build_opener()
    now = time.time() if now is None else now
    known = known_repos() if known is None else known
    pool = []
    for author in authors:
        try:
            listing = engine._get_json(LIST_URL % urllib.parse.quote(author), opener)
        except Exception:                                        # offline or the API changed: this account gives nothing this time
            continue
        for item in listing if isinstance(listing, list) else []:
            repo = item.get("id") or item.get("modelId") or ""
            name = repo.split("/", 1)[-1]
            size_b = modelpick._size_b(name)
            age = _age_days(item.get("lastModified"), now)
            if not repo or repo in known or modelpick.NON_CHAT.search(name) or NOT_TEXT.search(name) or not MIN_B <= size_b <= MAX_B:
                continue
            if age is None or age > RECENT_DAYS or modelpick.UNSTABLE.search(name):
                continue
            pool.append({"repo": repo, "name": name, "downloads": int(item.get("downloads") or 0), "age": age, "tags": item.get("tags") or []})
    pool.sort(key=lambda c: -c["downloads"])
    cards = []
    for entry in pool[:MAX_CARDS * 2]:
        card = _card(entry, hardware, opener)
        if card:
            cards.append(card)
        if len(cards) >= MAX_CARDS:
            break
    return cards


def _card(entry, hardware, opener):
    try:
        detail = engine._get_json(engine.HF_API % entry["repo"], opener)
    except Exception:
        return None
    if detail.get("gated") or detail.get("private"):
        return None                                              # cannot be downloaded automatically: not recommended
    files = [s for s in detail.get("siblings", []) if WANTED_FILE.search(s.get("rfilename", "")) and not SPLIT_FILE.search(s.get("rfilename", ""))]
    size = (files[0].get("size") or (files[0].get("lfs") or {}).get("size")) if files else None
    if not size:
        return None
    weights = size / 2**30
    license_tag = next((t[8:] for t in entry["tags"] + detail.get("tags", []) if isinstance(t, str) and t.startswith("license:")), "")
    fit, note, _memory, _ctx = engine.fit_with_context({"weights_gib": weights, "kv_bytes_per_token": None}, hardware)
    return {"id": "hf:" + entry["repo"], "kind": "model", "title": entry["name"].replace("-GGUF", ""),
            "summary": "최근 %d일 안에 갱신된 GGUF(Q4_K_M) 후보입니다. %s(KV 캐시 크기를 몰라 대략)" % (int(entry["age"]) + 1, note),
            "source_url": "https://huggingface.co/" + entry["repo"],
            "facts": {"제공": entry["repo"].split("/", 1)[0], "파일 크기": "%.2f GiB" % weights, "라이선스": license_tag or "확인 필요", "다운로드": "{:,}".format(entry["downloads"]), "파일": files[0]["rfilename"]},
            "fit": fit, "measured": False}


# ---- D3: release news from GitHub (rule-based; the notes are quoted, never interpreted) -----------------------------------------

NEWS_REPOS = (("sst/opencode", "OpenCode"), ("ollama/ollama", "Ollama"))
RELEASE_URL = "https://api.github.com/repos/%s/releases/latest"
NEWS_DAYS = 45
NOTE_CHARS = 280


def _plain(text):
    """The first lines of a release note as plain text: markdown marks, links and HTML removed, length capped. Never executed or sent to a model."""
    text = re.sub(r"<[^>]+>", " ", str(text or ""))
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[#*_`>]+", "", text)
    lines = [" ".join(line.split()) for line in text.splitlines() if line.strip()]
    return " / ".join(lines[:3])[:NOTE_CHARS]


def news(opener=None, now=None, repos=NEWS_REPOS):
    """-> news cards for the latest release of each repo, when it is recent and not a pre-release. A repo that cannot be read is skipped."""
    opener = opener or urllib.request.build_opener()
    now = time.time() if now is None else now
    cards = []
    for repo, label in repos:
        try:
            release = engine._get_json(RELEASE_URL % repo, opener)
        except Exception:
            continue
        if not isinstance(release, dict) or release.get("prerelease") or release.get("draft"):
            continue
        age = _age_days(release.get("published_at"), now)
        tag = str(release.get("tag_name") or "")
        url = str(release.get("html_url") or "")
        if age is None or age > NEWS_DAYS or not tag or not url.startswith("https://github.com/"):
            continue
        note = _plain(release.get("body"))
        cards.append({"id": "gh:%s:%s" % (repo, tag), "kind": "news", "title": "%s 새 릴리스 %s" % (label, tag),
                      "summary": "%d일 전 공개된 릴리스입니다. 릴리스 노트 발췌(외부 글이며 지시로 취급하지 않습니다): %s" % (int(age) + 1, note or "노트 없음"),
                      "source_url": url, "facts": {"제공": repo, "버전": tag}, "ai_summary": False})
    return cards


def discover(hardware, opener=None, now=None):
    """Everything one search finds: model candidates and release news."""
    return candidates(hardware, opener=opener, now=now) + news(opener=opener, now=now)
