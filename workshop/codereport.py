"""Per-run Markdown report and metrics.json for code work, so the next person or agent can pick the run up.

Built only from what JetAI recorded (run.json, events.jsonl, patch.diff). Secret-looking strings are masked.
Facts JetAI does not collect yet (build/test results, review) are stated as "not recorded", never invented.
"""
import json
import re
import time
from pathlib import Path

MAX_EVENT_BYTES = 8_000_000
SECRET = re.compile(r"(sk-[A-Za-z0-9_\-]{8,}|gh[pousr]_[A-Za-z0-9]{16,}|AIza[0-9A-Za-z_\-]{20,}|Bearer\s+[A-Za-z0-9._\-]{12,}|"
                    r"(?i:(?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s\"']{6,})")
STATUS_TEXT = {"review": "검토 대기", "nochange": "변경 없음", "failed": "실패", "cancelled": "취소됨", "applied": "적용됨",
               "undone": "적용 후 되돌림", "discarded": "버림", "running": "실행 중"}


def mask(text):
    return SECRET.sub("[가림]", text or "")


def _stamp(value):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value)) if value else "-"


def read_events(path):
    """Tool calls and token totals from OpenCode's JSON event stream (best effort: unknown shapes are ignored)."""
    tools, tokens, steps = {}, {"input": 0, "output": 0}, 0
    seen_tokens = False
    path = Path(path)
    if not path.exists():
        return {"tool_calls": 0, "tool_errors": 0, "tools": {}, "steps": 0, "tokens": None}
    errors = set()
    done = set()
    with open(path, "rb") as handle:
        for raw in handle.read(MAX_EVENT_BYTES).splitlines():
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            part = event.get("part") or {}
            if event.get("type") == "tool_use":
                state = part.get("state") or {}
                key = part.get("callID") or part.get("id") or len(done)
                if state.get("status") in ("completed", "error") and key not in done:
                    done.add(key)
                    tools[part.get("tool", "tool")] = tools.get(part.get("tool", "tool"), 0) + 1
                    if state["status"] == "error":
                        errors.add(key)
            if event.get("type") == "step_finish":
                steps += 1
                counts = part.get("tokens") or {}
                if isinstance(counts, dict):
                    for name in ("input", "output"):
                        if isinstance(counts.get(name), int):
                            tokens[name] += counts[name]
                            seen_tokens = True
    return {"tool_calls": len(done), "tool_errors": len(errors), "tools": tools, "steps": steps, "tokens": tokens if seen_tokens else None}


def build(run, events_path):
    """Return (markdown, metrics) for one run."""
    seconds = round(run["finished"] - run["started"], 1) if run.get("finished") and run.get("started") else None
    events = read_events(events_path)
    files = run.get("files") or []
    metrics = {"id": run["id"], "status": run["status"], "model": run.get("model"), "tier": run.get("tier"), "seconds": seconds,
               "files_changed": len(files), "lines_added": sum(f.get("added", 0) for f in files),
               "lines_deleted": sum(f.get("deleted", 0) for f in files), **events,
               "applied": bool(run.get("applied_at")), "undone": bool(run.get("undone_at")), "repo_was_dirty": bool(run.get("repo_was_dirty")), "checks": (run.get("checks") or {}).get("verdict") or None, "review": (run.get("review") or {}).get("verdict"),
               "cost": "무료/로컬" if run.get("tier") in ("local", "free") else "확인 필요(유료일 수 있음)"}
    lines = ["# 코드 작업 보고서 " + run["id"], "",
             "## 목표", mask(run.get("prompt", "")), "",
             "## 최종 판정", "- 상태: **%s**" % STATUS_TEXT.get(run["status"], run["status"])]
    if run.get("error"):
        lines.append("- 사유: " + mask(run["error"]))
    lines += ["- 시작 %s · 끝 %s · 소요 %s초" % (_stamp(run.get("started")), _stamp(run.get("finished")), seconds if seconds is not None else "-")]
    if run.get("applied_at"):
        lines.append("- 사용자가 내 폴더에 적용함: " + _stamp(run["applied_at"]) + " (커밋은 하지 않음)")
    if run.get("undone_at"):
        lines.append("- 적용을 되돌림: " + _stamp(run["undone_at"]))
    lines += ["", "## 모델·비용", "- 모델: `%s` (구분: %s, %s)" % (run.get("model"), run.get("tier"), metrics["cost"])]
    t = events["tokens"]
    lines.append("- 토큰: " + ("입력 %d / 출력 %d" % (t["input"], t["output"]) if t else "실행기가 알려 주지 않음(기록 없음)"))
    lines += ["", "## 프로젝트 요약(JetAI가 미리 읽음)", mask(run.get("project") or "(없음)"), "",
              "## 에이전트가 한 일", "- 도구 호출 %d회(오류 %d회): %s" % (events["tool_calls"], events["tool_errors"],
                                                              ", ".join("%s %d" % kv for kv in sorted(events["tools"].items())) or "-")]
    lines += ["- 마지막 활동:"] + ["  - " + mask(line)[:200] for line in (run.get("activity") or [])[-10:]]
    lines += ["", "## 막힌 명령과 승인"]
    blocked = run.get("blocked") or []
    names = {"open": "결정 안 함", "once": "이번만 허용", "always": "이 프로젝트에서 항상 허용", "declined": "거절"}
    lines += ["- `%s` — %s%s" % (mask(b["command"]), names.get(b["state"], b["state"]), "" if b["level"] == "normal" else " (" + b["note"] + ")")
              for b in blocked] or ["- 막힌 명령 없음"]
    lines += ["", "## 바뀐 파일 (%s)" % (run.get("stat") or "없음")]
    kinds = {"A": "추가", "M": "수정", "D": "삭제", "R": "이름변경"}
    lines += ["- %s `%s` %s" % (kinds.get(f.get("status"), f.get("status")), f["path"], "(바이너리)" if f.get("binary") else "+%d −%d" % (f["added"], f["deleted"])) for f in files] or ["- 없음"]
    lines += ["", "## 빌드·테스트·리뷰"]
    check = run.get("checks") or {}
    verdicts = {"passed": "통과", "failed": "실패", "none": "검사할 명령이 감지되지 않음", "unavailable": "실행 도구가 없어 못 함", "notests": "실행된 테스트가 없음(통과로 보지 않음)", "": "진행 중"}
    names = {"passed": "통과", "failed": "실패", "timeout": "시간 초과", "unavailable": "도구 없음", "notests": "테스트 없음"}
    if check:
        lines.append("- JetAI가 복사본에서 직접 실행한 결과: **%s**" % verdicts.get(check.get("verdict"), check.get("verdict")))
        lines += ["  - %s `%s`: %s (%s초)" % (c["kind"], c["command"], names.get(c["status"], c["status"]), c["seconds"]) for c in check.get("items", [])]
    else:
        lines.append("- 빌드·테스트: 기록 없음(사용자가 \"빌드·테스트 실행\"을 누르지 않았습니다).")
    review = run.get("review") or {}
    if review.get("state") == "done":
        verdict_names = {"ok": "문제 없어 보임", "caution": "주의", "problem": "문제 있음", "unknown": "판단 불가(형식을 못 읽음)"}
        lines += ["- 코드 리뷰(수정과 다른 모델 `%s`): **%s** — %s" % (review.get("model"), verdict_names.get(review.get("verdict"), "?"), mask(review.get("summary", "")))]
        lines += ["  - [%s] %s: %s" % (i["severity"], i["where"], i["text"]) for i in review.get("issues", [])]
        lines += ["  - 빠진 것: " + m for m in review.get("missing", [])]
    elif review.get("state") == "failed":
        lines.append("- 코드 리뷰: 실패 — " + mask(review.get("error", "")))
    else:
        lines.append("- 코드 리뷰: 기록 없음(사용자가 요청하지 않았습니다).")
    lines += ["", "## 주의", "- 작업 시작 때 폴더에 저장하지 않은 변경이 %s." % ("있었음(복사본에는 없음)" if run.get("repo_was_dirty") else "없었음"),
              "- 이 보고서는 기록된 사실만 담습니다. 비밀처럼 보이는 문자열은 가렸습니다.", ""]
    return "\n".join(lines), metrics
