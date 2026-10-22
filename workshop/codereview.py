"""Second-opinion review of a code-work change by a model other than the one that wrote it.

The reviewer only reads (request, build/test facts, diff) and reports; it never edits anything and its verdict never
blocks or triggers anything by itself. The diff is sent to the chosen service, so only local or free services the user
already agreed to are used, never a paid model.
"""
import json
import re

from . import chat, codereport

MAX_DIFF_CHARS = 24_000
MAX_ISSUES = 8
VERDICTS = ("ok", "caution", "problem")
SYSTEM = ("당신은 신중한 코드 리뷰어입니다. 사용자의 원래 요청, 빌드·테스트 결과, 변경 diff를 읽고 판단하세요. "
          "코드를 고치지 말고 보고만 합니다. 반드시 JSON 객체 하나로만 답하세요: "
          '{"verdict":"ok|caution|problem","summary":"한두 문장","issues":[{"severity":"high|medium|low","where":"파일/함수","text":"문제"}],'
          '"missing":["요청했지만 빠진 것"]}. 확실하지 않은 것은 caution으로 두고 추측으로 문제를 만들지 마세요.')


def build_messages(run, patch_text, check):
    facts = "빌드·테스트: 실행 안 함"
    if check:
        facts = "빌드·테스트(JetAI가 직접 실행): " + (check.get("verdict") or "진행 중") + "; " + ", ".join(
            "%s %s=%s" % (c["kind"], c["command"], c["status"]) for c in check.get("items", []))
    patch = codereport.mask(patch_text)
    cut = len(patch) > MAX_DIFF_CHARS
    user = "원래 요청:\n%s\n\n%s\n\n바뀐 파일: %s\n\ndiff%s:\n%s" % (
        codereport.mask(run.get("prompt", "")), facts, run.get("stat") or "-", " (길어서 앞부분만)" if cut else "", patch[:MAX_DIFF_CHARS])
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}]


def parse(text):
    """Tolerant JSON extraction; anything unreadable becomes an 'unknown' verdict that keeps the raw words."""
    text = (text or "").strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    data = None
    if match:
        try:
            data = json.loads(match.group(0))
        except ValueError:
            data = None
    if not isinstance(data, dict):
        return {"verdict": "unknown", "summary": codereport.mask(re.sub(r"\s+", " ", text))[:400], "issues": [], "missing": []}
    verdict = data.get("verdict") if data.get("verdict") in VERDICTS else "unknown"
    issues = []
    for item in (data.get("issues") or [])[:MAX_ISSUES]:
        if isinstance(item, dict):
            issues.append({"severity": item.get("severity") if item.get("severity") in ("high", "medium", "low") else "low",
                           "where": codereport.mask(str(item.get("where", "")))[:120], "text": codereport.mask(str(item.get("text", "")))[:300]})
    missing = [codereport.mask(str(m))[:200] for m in (data.get("missing") or [])[:MAX_ISSUES] if isinstance(m, (str, int))]
    return {"verdict": verdict, "summary": codereport.mask(str(data.get("summary", "")))[:400], "issues": issues, "missing": missing}


def review(provider, model, run, patch_text, check, timeout=180, chat_fn=chat.chat):
    answer = chat_fn(provider, model, build_messages(run, patch_text, check), timeout=timeout, max_tokens=900)
    return parse(answer)
