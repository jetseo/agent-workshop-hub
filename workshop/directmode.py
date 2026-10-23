"""Direct mode (Agentless-style): JetAI drives a fixed pipeline, the model only answers questions in plain text.

    1. localize   the model names the files to change (JetAI checks that they exist),
    2. edit       for each file the model writes SEARCH/REPLACE blocks; JetAI applies them (exact match, once),
    3. validate   JetAI runs the project's detected tests in the copy,
    4. repair     at most one more round: failed blocks or failing tests are shown to the model once.

Why: small and free models often cannot drive an agent's tool calls (measured on this PC), but they can usually write
a small diff as text. Nothing here lets the model run commands or touch paths outside the copy: JetAI applies the
text itself, only to files inside the work folder, never to secrets, `.git` or anything outside it.
See docs/multi-agent-plan.md (M1) and docs/direct-mode.md.
"""
import json
import re
from pathlib import Path

from . import logsum, projectmap

MAX_FILES = 3
# Answer budgets are ceilings, not costs. Reasoning ('thinking') models spend several hundred tokens before the answer:
# with a small ceiling their answer comes back empty (measured with gemma4:12b: ~800 tokens of thinking for a one-line reply).
LOCALIZE_TOKENS = 2500
WRITE_TOKENS = 6000
MAX_BLOCKS = 8
MAX_FILE_CHARS = 40_000
MAX_NEW_FILE_CHARS = 60_000
BLOCK = re.compile(r"<{5,9} SEARCH\r?\n(.*?)\r?\n={5,9}\r?\n(.*?)\r?\n?>{5,9} REPLACE", re.S)
FENCE = re.compile(r"```[a-zA-Z0-9_+-]*\r?\n(.*?)```", re.S)

LOCALIZE = ("You are a careful software engineer. Given a task and a list of repository files, decide which EXISTING files must be "
            "changed and which NEW files (if any) must be created. Reply with one JSON object only: "
            '{"files": ["path", ...], "new_files": ["path", ...], "reason": "one sentence"}. At most %d paths in total, '
            "use paths exactly as listed (new files: a sensible path), no other text. If the task asks to delete, wipe or empty files "
            'or the repository, or anything else destructive, reply {"refuse": true, "reason": "..."} instead.')
EDIT = ("You edit one file. Reply ONLY with SEARCH/REPLACE blocks, in this exact format, one block per change:\n"
        "<<<<<<< SEARCH\n(lines copied exactly from the file)\n=======\n(the new lines)\n>>>>>>> REPLACE\n"
        "Each SEARCH text must appear exactly once in the file and be long enough to be unique. Keep changes minimal and in the "
        "existing style. Do not change anything the task does not ask for. If your new code uses a name that is defined in another "
        "file, add the import. When you move or rename code, copy its body exactly (every character, punctuation included). No explanations.")
CREATE = ("You create one new file. Reply ONLY with the complete file content inside a single fenced code block. No explanations. "
          "If the task moves or copies code into this file, copy it exactly from the reference files (every character, punctuation included).")
MARKER_LINE = re.compile(r"^\s*(?:<{5,9}\s*SEARCH|={5,9}\s*$|>{5,9}\s*REPLACE|SEARCH\s*$|FILE:)", re.M)       # edit-block syntax is never file content


class DirectError(Exception):
    pass


def safe_path(worktree, relative):
    """The path inside the work folder, or None: relative, no `..`, no `.git`, no secret-looking names."""
    if not isinstance(relative, str) or not relative or len(relative) > 300 or "\x00" in relative:
        return None
    text = relative.replace("\\", "/")
    parts = text.split("/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or any(p in ("", ".", "..") for p in parts) or ".git" in parts:
        return None
    if projectmap._secret(parts[-1]):
        return None
    root = Path(worktree).resolve()
    target = (root / text).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def parse_files(answer, listed):
    """Files and new files from the model's answer (tolerant: JSON anywhere in the text, else listed paths found in it)."""
    match = re.search(r"\{.*\}", answer or "", flags=re.S)
    data = None
    if match:
        try:
            data = json.loads(match.group(0))
        except ValueError:
            data = None
    files, new = [], []
    if isinstance(data, dict):
        files = [f for f in (data.get("files") or []) if isinstance(f, str)]
        new = [f for f in (data.get("new_files") or []) if isinstance(f, str)]
    else:
        files = [p for p in listed if p in (answer or "")]
    named = [f.replace("\\", "/") for f in files + new]              # small models put a new file under "files": sort by whether it exists
    existing = [f for f in named if f in set(listed)]
    fresh = [f for f in named if f not in set(listed)]
    out_existing, out_new = [], []
    for item in existing:
        if item not in out_existing:
            out_existing.append(item)
    for item in fresh:
        if item not in out_new and item not in out_existing:
            out_new.append(item)
    return out_existing[:MAX_FILES], out_new[:max(0, MAX_FILES - len(out_existing))]


def parse_blocks(answer):
    return [(m.group(1), m.group(2)) for m in BLOCK.finditer(answer or "")][:MAX_BLOCKS]


def _lines_match(text, search):
    """(start, end) of `search` in `text`: exact once, else once ignoring trailing spaces on each line; None otherwise."""
    count = text.count(search)
    if count == 1:
        index = text.index(search)
        return index, index + len(search)
    if count > 1:
        return None
    t_lines = text.split("\n")
    s_lines = [line.rstrip() for line in search.split("\n")]
    hits = [i for i in range(len(t_lines) - len(s_lines) + 1) if [x.rstrip() for x in t_lines[i:i + len(s_lines)]] == s_lines]
    if len(hits) != 1:
        return None
    offset = sum(len(line) + 1 for line in t_lines[:hits[0]])
    return offset, offset + len("\n".join(t_lines[hits[0]:hits[0] + len(s_lines)]))


def _append(text, addition):
    gap = "" if (not text or text.endswith("\n")) else "\n"
    return text + gap + addition + ("" if addition.endswith("\n") else "\n")


def apply_blocks(text, blocks):
    """-> (new_text, applied, failed_blocks). Text uses \\n only; the caller restores BOM and line endings."""
    applied, failed = 0, []
    for search, replace in blocks:
        if not search.strip():                                  # an empty SEARCH means "add this at the end of the file"
            if replace.strip():
                text = _append(text, replace)
                applied += 1
            else:
                failed.append("(empty)")
            continue
        found = _lines_match(text, search)
        if found is None:
            failed.append(search[:120])
            continue
        text = text[:found[0]] + replace + text[found[1]:]
        applied += 1
    return text, applied, failed


def read_text(path):
    raw = Path(path).read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw[3:].decode("utf-8", "replace") if bom else raw.decode("utf-8", "replace")
    crlf = "\r\n" in text
    return text.replace("\r\n", "\n"), bom, crlf


def write_text(path, text, bom, crlf):
    data = text.replace("\n", "\r\n") if crlf else text
    Path(path).write_bytes((b"\xef\xbb\xbf" if bom else b"") + data.encode("utf-8"))


def _ask(chat, system, user, max_tokens, temperature=None):
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return chat(messages, max_tokens) if temperature is None else chat(messages, max_tokens, temperature=temperature)


def is_test_path(path):
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or path.startswith("tests/") or "/tests/" in path


def related_tests(worktree, files, exclude=(), limit=8000, targets=()):
    """Read-only context: the project's test files (small ones, up to `limit` characters in all), so the model sees what is expected.

    With `targets` (the files being edited) only tests that import one of those modules are sent when any do: fewer input tokens."""
    parts, total = [], 0
    candidates = [f for f in files if is_test_path(f) and f not in exclude][:30]
    stems = {rel.rsplit("/", 1)[-1].rsplit(".", 1)[0] for rel in targets}
    if stems:
        pattern = re.compile(r"(?:^|\s)(?:import|from)\s+[\w.]*(?<![\w])(?:%s)(?![\w])" % "|".join(re.escape(s) for s in stems), re.M)
        mentioning = [f for f in candidates if (p := safe_path(worktree, f)) is not None and p.is_file() and pattern.search(read_text(p)[0])]
        candidates = mentioning or candidates
    for rel in candidates[:8]:
        path = safe_path(worktree, rel)
        if path is None or not path.is_file():
            continue
        text, _, _ = read_text(path)
        if total + len(text) > limit:
            continue
        parts.append("--- %s\n%s" % (rel, text))
        total += len(text)
    return "\n".join(parts)


def run(chat, task, worktree, files, summary, test_fn, emit, *, cancelled=lambda: False, validate=True, context="", temperature=None, context_for=None):
    """Run the pipeline in `worktree`. `chat(messages, max_tokens) -> str`, `test_fn() -> (verdict, output)`, `emit(tool, ok, text)`.

    Returns {"ok", "error", "changed", "tests", "calls"}; it never raises for model mistakes (they become `error`)."""
    result = {"ok": False, "error": "", "changed": [], "tests": "skipped", "calls": 0}
    ctx = ("\n\nRelated tests (read-only, do not edit them):\n" + context) if context else ""

    def ask(system, user, tokens):
        result["calls"] += 1
        return _ask(chat, system, user, tokens, temperature)

    def stopped():
        if cancelled():
            result["error"] = "사용자가 취소했습니다."
            return True
        return False
    try:
        listing = "\n".join(files[:400]) + ("\n… (%d more)" % (len(files) - 400) if len(files) > 400 else "")
        answer = ask(LOCALIZE % MAX_FILES, "Task:\n%s\n\nProject summary:\n%s\n\nFiles:\n%s" % (task, summary, listing), LOCALIZE_TOKENS)
        if re.search(r'"refuse"\s*:\s*true', answer or "", flags=re.I):
            emit("localize", False, "위험한 요청이라 이행하지 않음")
            result["error"] = "요청이 파일 삭제·초기화 같은 위험한 작업이라 이행하지 않았습니다."
            return result
        existing, new = parse_files(answer, files)
        protected = [f for f in existing if is_test_path(f)]
        if protected and (len(existing) > len(protected) or new):       # tests are the yardstick: keep existing ones read-only when other files can be edited
            existing = [f for f in existing if f not in protected]
        else:
            protected = []
        if context_for is not None:                                  # now that the files are known, send only the tests that matter
            context = context_for(existing)
            ctx = ("\n\nRelated tests (read-only, do not edit them):\n" + context) if context else ""
        if not existing and not new:
            emit("localize", False, "수정할 파일을 정하지 못했습니다")
            result["error"] = "모델이 수정할 파일을 정하지 못했습니다(답: %s)" % re.sub(r"\s+", " ", answer or "")[:120]
            return result
        emit("localize", True, ", ".join(existing + new) + (" (기존 테스트 파일은 고치지 않음: %s)" % ", ".join(protected) if protected else ""))
        if stopped():
            return result
        original = {}
        for rel in existing:
            path = safe_path(worktree, rel)
            if path is None or not path.is_file():
                emit("edit", False, rel + " (허용되지 않는 경로)")
                result["error"] = "허용되지 않거나 없는 파일 경로입니다: " + rel
                return result
            text, bom, crlf = read_text(path)
            if len(text) > MAX_FILE_CHARS:
                emit("edit", False, rel + " (너무 큼)")
                result["error"] = "파일이 너무 커서 직접 모드로 고칠 수 없습니다: " + rel
                return result
            original[rel] = (text, bom, crlf)
        done_notes = []                                                  # what earlier files now contain, shown to the model when it edits the next one
        for rel in new:
            if stopped():
                return result
            path = safe_path(worktree, rel)
            if path is None or path.exists():
                emit("create", False, rel + " (허용되지 않는 경로 또는 이미 있음)")
                result["error"] = "만들 수 없는 파일 경로입니다: " + rel
                return result
            refs = "".join("\n\nReference file %s (current content):\n```\n%s\n```" % (r, original[r][0][:3000]) for r in existing)   # the new file may need code that lives in these
            answer = ask(CREATE, "Task:\n%s\n\nNew file path: %s\nExisting files:\n%s%s" % (task, rel, "\n".join(files[:80]), refs), WRITE_TOKENS)
            match = FENCE.search(answer or "")
            body = match.group(1) if match else ""
            if not body.strip() or len(body) > MAX_NEW_FILE_CHARS or MARKER_LINE.search(body):
                emit("create", False, rel + " (내용을 읽지 못함)")
                result["error"] = "새 파일 내용을 읽지 못했습니다: " + rel
                return result
            path.parent.mkdir(parents=True, exist_ok=True)
            write_text(path, body if body.endswith("\n") else body + "\n", False, False)
            emit("create", True, rel)
            result["changed"].append(rel)
            done_notes.append("New file %s now contains:\n%s" % (rel, body[:800]))
        for rel in existing:
            if stopped():
                return result
            ok = _edit_file(ask, task, worktree, rel, original[rel], emit, retry=True, ctx=ctx + _earlier(done_notes))
            if not ok:
                result["error"] = "%s 에 수정안을 적용하지 못했습니다." % rel
                return result
            result["changed"].append(rel)
            now = read_text(safe_path(worktree, rel))[0]
            done_notes.append("Changed %s:\n%s" % (rel, _diff_note(original[rel][0], now, rel)))
        if not validate:
            result["ok"] = True
            return result
        verdict, output = test_fn()
        emit("test", verdict in ("passed", "none", "notests", "unavailable"), "결과: " + verdict)
        result["tests"] = verdict
        if verdict == "failed" and not stopped():
            for rel in existing:
                text, bom, crlf = read_text(safe_path(worktree, rel))
                original[rel] = (text, bom, crlf)
            fixed = _repair(ask, task, worktree, existing, original, output, emit, ctx + _earlier(done_notes))
            if fixed:
                verdict, output = test_fn()
                emit("test", verdict == "passed", "수정 뒤 결과: " + verdict)
                result["tests"] = verdict
        result["ok"] = True
        return result
    except Exception as error:                                   # the service failed or answered nonsense: report, never crash
        result["error"] = "모델 호출 실패: " + str(error)[:200]
        emit("localize" if not result["changed"] else "edit", False, result["error"])
        return result


def _earlier(notes):
    """A short block listing the changes already made in other files, so the next edit can use their names."""
    if not notes:
        return ""
    return "\n\nAlready changed in this task (use these names, do not redo them):\n" + "\n\n".join(notes)[:2500]


def _diff_note(before, after, rel):
    import difflib
    lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), "a/" + rel, "b/" + rel, n=1, lineterm=""))
    return "\n".join(lines)[:1200]


def _edit_file(ask, task, worktree, rel, current, emit, retry, ctx=""):
    text, bom, crlf = current
    path = safe_path(worktree, rel)
    prompt = "Task:\n%s\n\nFile: %s\n```\n%s\n```%s" % (task, rel, text, ctx)

    def attempt(extra=""):
        """-> (new_text or None, problem, note). new_text is set only for a usable change."""
        answer = ask(EDIT, prompt + extra, WRITE_TOKENS)
        blocks = parse_blocks(answer)
        if blocks:
            candidate, applied, failed = apply_blocks(text, blocks)
            if failed or not applied:
                return None, "These SEARCH blocks did not match exactly once, copy the lines exactly: " + " | ".join(f.replace("\n", "\\n") for f in failed), ""
            note = "%d개 블록" % applied
        else:
            whole = FENCE.search(answer or "")          # the model rewrote the whole file instead: accept it for a small file, the diff shows the result
            if not whole or not whole.group(1).strip() or len(whole.group(1)) > MAX_FILE_CHARS:
                return None, "Your reply had no SEARCH/REPLACE blocks in the required format.", ""
            if MARKER_LINE.search(whole.group(1)):
                return None, "Your reply put SEARCH/REPLACE markers inside file content. Reply with proper SEARCH/REPLACE blocks only.", ""
            candidate, note = whole.group(1).replace("\r\n", "\n"), "파일 전체를 다시 씀"
            candidate = candidate if candidate.endswith("\n") else candidate + "\n"
        if rel.endswith(".py"):
            try:
                compile(candidate, rel, "exec")
            except SyntaxError as error:                       # catch a broken file now, while the model still has the context
                return None, "Your change makes the file invalid Python (line %s: %s). Fix it." % (error.lineno, error.msg), ""
        if candidate == text:
            return None, "Your change left the file unchanged. Make the change the task asks for.", ""
        if len(text) > 200 and len(candidate.strip()) < len(text.strip()) * 0.2:
            return None, "Your change removes almost all of the file's content, which is not allowed.", ""
        return candidate, "", note
    result, problem, note = attempt()
    if result is None and retry:
        result, problem, note = attempt("\n\n" + problem)
    if result is None:
        emit("edit", False, "%s (%s)" % (rel, re.sub(r"\s+", " ", problem)[:90]))
        return False
    write_text(path, result, bom, crlf)
    emit("edit", True, "%s (%s)" % (rel, note))
    return True


def _repair(ask, task, worktree, existing, original, output, emit, ctx=""):
    any_fixed = False
    for rel in existing:
        text, bom, crlf = original[rel]
        prompt = ("Task:\n%s\n\nThe project's tests fail after the change. Test output:\n%s\n\nCurrent file %s:\n```\n%s\n```\n"
                  "Fix the file so the tests pass. Reply with SEARCH/REPLACE blocks only.") % (task, logsum.reduce_log(output or "", 1800)["text"], rel, text) + ctx
        blocks = parse_blocks(ask(EDIT, prompt, WRITE_TOKENS))
        new_text, applied, failed = apply_blocks(text, blocks)
        if applied and not failed:
            write_text(safe_path(worktree, rel), new_text, bom, crlf)
            emit("repair", True, "%s (%d개 블록)" % (rel, applied))
            any_fixed = True
        else:
            emit("repair", False, rel + " (고치지 못함)")
    return any_fixed
