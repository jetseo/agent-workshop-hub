"""Tidy a service's raw model list and recommend a few models, so nobody has to study 60+ names.

Services list everything they host: speech, image, embedding and "preview" models next to the chat models.
`curate` drops what cannot answer a chat message, strips the "models/" prefix some services add, and
recommends a small set (fast + strong + coder) that the auto mode then chooses among. Name heuristics only:
unknown naming schemes fall back to "first few". Nothing here calls a service.
"""
import re

# Cannot answer a normal chat request (speech, images, embeddings, safety classifiers, realtime/live, ...).
NON_CHAT = re.compile(
    r"embed|tts|text-to-speech|speech|audio|whisper|transcri|imagen|image|dall-?e|veo|sora|flux|sdxl|stable-diffusion|"
    r"rerank|moderat|guard|safety|classif|robotics|aqa|ocr|live|computer-use|realtime|vision-only|lyria|music", re.I)
UNSTABLE = re.compile(r"preview|exp(?:erimental)?\b|-exp-|beta|alpha|deprecated", re.I)
FAST = re.compile(r"(?<![a-z])(flash|lite|mini|small|instant|nano|haiku|turbo|fast)(?![a-z])", re.I)   # not "gemini"
STRONG = re.compile(r"(?<![a-z])(pro|large|opus|sonnet|maverick|ultra|max|plus|thinking|reason)(?![a-z])", re.I)
CODER = re.compile(r"cod(?:er|estral|ex)|devstral", re.I)
MAX_RECOMMENDED = 5


INFO_LOOKUP = None      # set by modelinfo.install(): name -> {"level", "code", "reasoning", "ctx", "vision"} or None
LIGHT_MODEL = re.compile(r"(?<![a-z])(lite|mini|nano|small|instant|haiku|tiny|fast)(?![a-z])", re.I)
FLASH_MODEL = re.compile(r"(?<![a-z])flash(?![a-z])", re.I)
REASONING = re.compile(r"(?<![a-z])(r1|reason\w*|thinking|think|qwq|o1|o3|o4|deepseek-r)(?![a-z])", re.I)


def capability(model):
    """Rough strength of a model from its name: {"level": 1 light | 2 standard | 3 strong, "coder", "reasoning"}.
    Names are all there is to go on (no benchmark data), so unknown names count as standard (2)."""
    name = clean_name(model).split("/")[-1]
    size = _size_b(name)
    if LIGHT_MODEL.search(name):
        level = 1
    elif STRONG.search(name) or size >= 60:
        level = 3
    elif FLASH_MODEL.search(name):
        level = 2
    elif 0 < size <= 15:
        level = 1
    else:
        level = 2
    cap = {"level": level, "coder": bool(CODER.search(name)), "reasoning": bool(REASONING.search(name)), "evidence": False}
    facts = INFO_LOOKUP(model) if INFO_LOOKUP else None      # public catalog data beats a guess from the name
    if facts:
        cap["evidence"] = True
        measured = facts.get("level")           # benchmark percentile, blended with the name-based guess (a "70b" can mislead)
        cap["level"] = int((level + measured) / 2 + 0.5) if measured else level
        cap["coder"] = cap["coder"] or bool(facts.get("code"))
        cap["reasoning"] = cap["reasoning"] or bool(facts.get("reasoning"))
    return cap


def clean_name(model):
    return model[len("models/"):] if isinstance(model, str) and model.startswith("models/") else model


def _size_b(name):
    match = re.search(r"(\d+(?:\.\d+)?)b\b", name.lower())
    return float(match.group(1)) if match else 0.0


def _version(name):
    """Numbers in the name as a sortable tuple, so gemini-2.5 outranks gemini-1.5 and 3 outranks 2.5."""
    return tuple(float(n) for n in re.findall(r"\d+(?:\.\d+)?", re.sub(r"\d+b\b", "", name.lower()))[:3])


def chat_models(names):
    """Cleaned, de-duplicated names of models that can chat, newest-looking first."""
    seen, out = set(), []
    for raw in names or []:
        if not isinstance(raw, str):
            continue
        name = clean_name(raw.strip())
        if not name or name in seen or NON_CHAT.search(name):
            continue
        seen.add(name)
        out.append(name)
    return sorted(out, key=lambda n: (bool(UNSTABLE.search(n)), tuple(-v for v in _version(n)), n))


def _family(name):
    """Vendor/series prefix ("gemini", "gemma", "meta-llama/llama"), so picks can come from different series."""
    return re.split(r"[-_.]?\d", name, maxsplit=1)[0]


def _diverse(items, count):
    """First `count` items, taking one per series before a second from the same series (input is ranked)."""
    out, seen = [], set()
    for n in items:
        if len(out) < count and _family(n) not in seen:
            seen.add(_family(n))
            out.append(n)
    for n in items:
        if len(out) < count and n not in out:
            out.append(n)
    return out


def recommend(names, limit=MAX_RECOMMENDED):
    """A small, varied set: the newest fast models, the newest strong models and one coder model."""
    stable = [n for n in chat_models(names) if not UNSTABLE.search(n) and not re.search(r"latest", n, re.I)]   # aliases move; pin versions
    if not stable:
        return chat_models(names)[:limit]

    def kind(n):
        size = _size_b(n)
        if CODER.search(n):
            return "coder"
        if FAST.search(n) or (0 < size <= 15):
            return "fast"
        if STRONG.search(n) or size >= 30:
            return "strong"
        return "other"

    groups = {"fast": [], "strong": [], "coder": [], "other": []}
    for n in stable:
        groups[kind(n)].append(n)
    picks = _diverse(groups["fast"], 2) + _diverse(groups["strong"], 2) + groups["coder"][:1]
    for n in groups["other"]:                       # unknown naming: still offer something
        if len(picks) >= limit:
            break
        picks.append(n)
    seen, out = set(), []
    for n in picks:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out[:limit]


def curate(names):
    """{"models": cleaned chat models, "recommended": [...], "total": raw count, "hidden": how many were dropped}."""
    raw = [n for n in (names or []) if isinstance(n, str)]
    models = chat_models(raw)
    return {"models": models, "recommended": recommend(raw), "total": len(raw), "hidden": len(raw) - len(models)}
