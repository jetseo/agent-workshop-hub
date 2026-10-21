"""Compress long text with a free/local model so a paid tool has far fewer tokens to read.

  python -m workshop.digest build.log --model ollama/llama3.2:3b [--focus "errors and failed tests"] [--out digest.md]

Long input is split into chunks that are summarised one by one (and, if the result is still long, once more).
The digest can be wrong or drop details: keep the original and check anything important against it.
"""
import argparse
import sys
from pathlib import Path

from .chat import chat
from .logsum import reduce_log
from .usage import UsageLedger, estimate_tokens, tier_of

MAX_INPUT = 300_000
CHUNK_CHARS = 8_000
MAX_CHUNKS = 40
MAX_COMBINED = 12_000
MAX_PASSES = 2
MAX_FOCUS = 200
REPLY_TOKENS = 700

PROMPT = """다음 텍스트({part})를 핵심만 남겨 압축하세요.
규칙:
- 입력과 같은 언어로, 불릿(-) 목록으로 쓰세요.
- 오류 메시지, 파일 경로, 줄 번호, 숫자, 식별자, 명령어는 바꾸지 말고 원문 그대로 보존하세요.
- 없는 내용을 추측하거나 지어내지 마세요. 확실하지 않으면 생략하세요.
- 같은 내용의 반복은 한 번만 쓰고, 횟수가 중요하면 "(N회 반복)"으로 표시하세요.{focus}
- 설명이나 머리말 없이 압축 결과만 출력하세요.

--- 텍스트 시작 ---
{text}
--- 텍스트 끝 ---"""


def split_chunks(text, size=CHUNK_CHARS):
    """Pieces of about `size` characters, cut at line breaks when possible. "".join(pieces) == text."""
    pieces, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = text.rfind("\n", start + size // 2, end)
            if cut != -1:
                end = cut + 1
        pieces.append(text[start:end])
        start = end
    return pieces


def build_prompt(chunk, index, total, focus=""):
    part = "조각 %d/%d" % (index, total) if total > 1 else "전체"
    focus_line = ("\n- 특히 다음에 해당하는 내용을 우선 남기세요: " + focus.strip()) if focus.strip() else ""
    return PROMPT.format(part=part, focus=focus_line, text=chunk)


def stream(*, resolve, candidates, chat_fn, ledger, body, claim=None, release=None):
    """Validate eagerly (raises ValueError/PermissionError), then return a generator of (event, data):
    meta, progress, notice, done, error. `resolve(ref)` -> (provider, model); `candidates(ref, enabled)` -> refs."""
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("압축할 글을 입력하세요.")
    if len(text) > MAX_INPUT:
        raise ValueError("글이 너무 깁니다 (최대 %s자)." % format(MAX_INPUT, ","))
    focus = body.get("focus") or ""
    if not isinstance(focus, str) or len(focus) > MAX_FOCUS:
        raise ValueError("중점 사항은 %d자 이내로 쓰세요." % MAX_FOCUS)
    model_ref = body.get("model")
    provider, model = resolve(model_ref)
    original, prefilter = text, None
    if body.get("prefilter") is True:       # deterministic log reducer first: fewer tokens reach any model
        reduced = reduce_log(text)
        if reduced["text"].strip():
            text, prefilter = reduced["text"], reduced["stats"]
    chunks = split_chunks(text)
    if len(chunks) > MAX_CHUNKS:
        raise ValueError("조각이 너무 많습니다.")
    if tier_of(provider["data_policy"], model) == "other" and body.get("confirm_cost") is not True:
        raise PermissionError("요금이 나올 수 있는 모델입니다. 확인 체크를 한 뒤에만 실행됩니다.")
    allow_fallback = body.get("fallback", True) is not False

    def events():
        if claim is not None and not claim():
            yield "error", {"message": "이미 압축을 진행 중입니다."}
            return
        try:
            refs = candidates(model_ref, allow_fallback)
            used = {"index": 0, "requests": 0, "spent": 0}
            yield "meta", {"chunks": len(chunks), "model": model_ref}

            def call(prompt):
                """Ask the current model; on failure move to the next candidate and keep using it (sticky)."""
                attempts = []
                while used["index"] < len(refs):
                    ref = refs[used["index"]]
                    try:
                        prov, mod = resolve(ref)
                        reply = (chat_fn(prov, mod, [{"role": "user", "content": prompt}], max_tokens=REPLY_TOKENS) or "").strip()
                        if not reply:
                            raise RuntimeError("모델이 빈 응답을 돌려주었습니다.")
                    except Exception as error:
                        message = str(getattr(error, "message", error))[:200]
                        attempts.append(ref + ": " + message)
                        used["index"] += 1
                        if used["index"] < len(refs):
                            yield "notice", {"model": refs[used["index"]], "message": ref + " 실패(" + message + ") → " + refs[used["index"]] + "(으)로 전환합니다."}
                        continue
                    tokens_in, tokens_out = estimate_tokens(prompt), estimate_tokens(reply)
                    used["requests"] += 1
                    used["spent"] += tokens_in + tokens_out
                    if ledger is not None:
                        try:
                            ledger.record(provider=ref.partition("/")[0], model=mod, tier=tier_of(prov["data_policy"], mod),
                                          prompt_tokens=tokens_in, completion_tokens=tokens_out, estimated=True, source="digest")
                        except OSError:
                            pass
                    return reply, ref
                raise RuntimeError(attempts[0].split(": ", 1)[1] if len(attempts) == 1 else "모든 모델이 실패했습니다: " + "; ".join(attempts))

            current, passes, total_done = chunks, 0, 0
            while True:
                passes += 1
                partials = []
                for number, chunk in enumerate(current, 1):
                    reply, ref = yield from call(build_prompt(chunk, number, len(current), focus))
                    partials.append(reply)
                    total_done += 1
                    yield "progress", {"done": number, "total": len(current), "pass": passes}
                combined = "\n\n".join(partials)
                if len(current) == 1 or len(combined) <= MAX_COMBINED or passes >= MAX_PASSES:
                    break
                current = split_chunks(combined)
            before, after = estimate_tokens(original), estimate_tokens(combined)
            yield "done", {"digest": combined, "model": refs[min(used["index"], len(refs) - 1)], "requests": used["requests"],
                           "before_tokens": before, "after_tokens": after, "spent_tokens": used["spent"],
                           "reduction_pct": round(100 * (1 - after / before)) if before else 0,
                           "passes": passes, "still_long": len(combined) > MAX_COMBINED, "prefilter": prefilter}
        except GeneratorExit:
            raise
        except Exception as error:
            yield "error", {"message": str(error)[:600]}
        finally:
            if release is not None:
                release()

    return events()


def main(argv=None):
    from .orchestrator import SECRET_NAME_PARTS
    from .providers import Providers
    parser = argparse.ArgumentParser(description="Compress long files with a free/local model before sending them to a paid tool")
    parser.add_argument("files", nargs="+", help="text files to compress (their contents are sent to the chosen model)")
    parser.add_argument("--model", required=True, help="provider/model of a registered service, e.g. ollama/llama3.2:3b")
    parser.add_argument("--focus", default="", help="what matters most (e.g. 'errors and failed tests')")
    parser.add_argument("--out", help="write the digest to this file instead of stdout")
    parser.add_argument("--no-fallback", action="store_true", help="do not switch to another free/local model on errors")
    parser.add_argument("--allow-cloud", action="store_true", help="allow sending the text to a cloud service")
    parser.add_argument("--allow-may-train", action="store_true", help="accept that the service may train on it")
    parser.add_argument("--confirm-cost", action="store_true", help="allow a model that may charge money")
    args = parser.parse_args(argv)
    registry = Providers()
    try:
        parts = []
        for name in args.files:
            path = Path(name)
            if any(p in path.name.lower() for p in SECRET_NAME_PARTS):
                raise ValueError("Refusing a secret-looking file: " + name)
            if not path.is_file():
                raise ValueError("Not a file: " + name)
            parts.append(("==== " + path.name + " ====\n" if len(args.files) > 1 else "") + path.read_text(encoding="utf-8", errors="replace"))
        text = "\n".join(parts)

        def resolve(ref):
            if not isinstance(ref, str) or "/" not in ref:
                raise ValueError("--model must look like provider/model")
            entry = registry.get(ref.partition("/")[0])
            if entry is None:
                raise ValueError("Provider is not registered (python -m workshop.onboard add)")
            if entry["data_policy"] != "local" and not (args.allow_cloud and args.allow_may_train):
                raise PermissionError("The text would go to a cloud service that may train on it; pass --allow-cloud --allow-may-train")
            return entry, ref.partition("/")[2]

        def candidates(ref, enabled):
            if not enabled:
                return [ref]
            others = [name + "/" + m for name, e in registry.load().items() if e["data_policy"] == "local" for m in e.get("models", []) if name + "/" + m != ref]
            return [ref] + others[:3]

        result = None
        for event, data in stream(resolve=resolve, candidates=candidates, chat_fn=chat, ledger=UsageLedger(), body={
                "text": text, "model": args.model, "focus": args.focus, "fallback": not args.no_fallback,
                "confirm_cost": True if args.confirm_cost else None}):
            if event == "meta":
                print("조각 %d개를 %s로 압축합니다…" % (data["chunks"], data["model"]), file=sys.stderr)
            elif event == "progress":
                print("  %d/%d (패스 %d)" % (data["done"], data["total"], data["pass"]), file=sys.stderr)
            elif event == "notice":
                print("  " + data["message"], file=sys.stderr)
            elif event == "error":
                raise RuntimeError(data["message"])
            elif event == "done":
                result = data
    except (ValueError, PermissionError, RuntimeError, OSError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(result["digest"] + "\n", encoding="utf-8")
    else:
        print(result["digest"])
    print("원문 약 %s 토큰 → 압축본 약 %s 토큰 (%d%% 감소, 추정). 압축에 쓴 토큰 약 %s, 요청 %d회. 요약은 틀리거나 빠뜨릴 수 있으니 원문과 대조하세요." % (
        format(result["before_tokens"], ","), format(result["after_tokens"], ","), result["reduction_pct"],
        format(result["spent_tokens"], ","), result["requests"]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
