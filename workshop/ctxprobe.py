"""Find out how much input a LOCAL model service really accepts, and notice when input was silently cut.

Some local servers (Ollama's default) keep only about 2,000 prompt tokens however long the request is. The reply then
looks fine but the model never saw the rest. The only honest signal is the `prompt_tokens` the service reports: code
averages 3-4 characters per token, so many more characters per reported token means the input was cut.

Read-only: this only sends two small test prompts to a service on this PC. It never changes the service's settings;
`advice` tells the user how they could raise the limit themselves.
"""
import json
import threading
import time
from pathlib import Path

CHARS_PER_TOKEN_LIMIT = 6        # above this many characters per reported token the input was almost certainly cut
MIN_TOKENS = 512                 # tiny prompts say nothing
PROBE_CHARS = 30_000

ADVICE = ("입력이 잘리고 있습니다. JetAI는 이 서비스의 설정을 바꾸지 않습니다. 직접 늘리려면: Ollama라면 환경 변수 OLLAMA_CONTEXT_LENGTH=16384(또는 더 크게)를 "
          "설정한 뒤 Ollama를 다시 시작하거나, 문맥을 키운 모델 변체를 만드세요(`ollama create 이름 -f Modelfile`, 파일에 `PARAMETER num_ctx 16384`). "
          "문맥을 키우면 메모리(VRAM)를 더 씁니다. JetAI 내장 엔진은 실행할 때 문맥 크기를 직접 정합니다.")


def looks_cut(chars, prompt_tokens):
    """True when the reported input tokens are far too few for the characters sent."""
    return bool(prompt_tokens and prompt_tokens >= MIN_TOKENS and chars / prompt_tokens > CHARS_PER_TOKEN_LIMIT)


def _filler(chars):
    line = "def helper_%d(value):\n    return value + %d\n\n"
    out, i = [], 0
    while sum(len(x) for x in out) < chars:
        out.append(line % (i, i))
        i += 1
    return "".join(out)


def probe(chat_fn, provider, model, *, timeout=120, clock=time.time):
    """-> {"small_tokens", "big_tokens", "big_chars", "truncated", "limit_tokens", "advice", "checked_at"}"""
    seen = {}
    for label, chars in (("small", 800), ("big", PROBE_CHARS)):
        text = _filler(chars) + "\nReply with the single word OK."
        usage = {}
        chat_fn(provider, model, [{"role": "user", "content": text}], timeout=timeout, max_tokens=16, usage=usage)
        seen[label] = (len(text), usage.get("prompt_tokens"))
    big_chars, big_tokens = seen["big"]
    if not big_tokens:
        return {"ok": False, "error": "이 서비스는 입력 토큰 수를 알려 주지 않아 점검할 수 없습니다.", "checked_at": clock()}
    cut = looks_cut(big_chars, big_tokens)
    return {"ok": True, "small_tokens": seen["small"][1], "big_tokens": big_tokens, "big_chars": big_chars, "truncated": cut,
            "limit_tokens": big_tokens if cut else None, "advice": ADVICE if cut else "", "checked_at": clock()}


class ContextLimits:
    """Probe results per model reference (`service/model`), kept beside the other JetAI data."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, ref):
        with self._lock:
            entry = self._load().get(ref)
        return entry if isinstance(entry, dict) else None

    def limit_tokens(self, ref):
        entry = self.get(ref)
        return (entry or {}).get("limit_tokens")

    def set(self, ref, result):
        with self._lock:
            data = self._load()
            data[ref] = result
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
