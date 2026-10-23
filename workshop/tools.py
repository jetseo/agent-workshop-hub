"""Tool layer for the agent loop: small, bounded tools that act only inside the work folder, after `policy` allowed them.

Every result is short on purpose (a model's context is its cost): reads return numbered line ranges, searches a
handful of hits, command output only its tail. Nothing here uses a shell.
"""
import re
import shlex
import shutil
import subprocess
import sys
import time

from . import directmode

READ_LINES = 120            # default lines per read
READ_CHARS = 5000
LIST_NAMES = 80
SEARCH_HITS = 25
SEARCH_FILE_CHARS = 200_000
RUN_TAIL = 1500
RUN_TIMEOUT = 120


def _clip(text, limit):
    return text if len(text) <= limit else text[:limit] + "\n… (잘림)"


class ToolBox:
    def __init__(self, worktree, files, policy, *, popen=subprocess.Popen, kill=None, env=None, clock=time.time):
        self.worktree, self.files, self.policy = worktree, list(files), policy
        self.popen, self.kill, self.env, self.clock = popen, kill, env or {}, clock
        self.changed = []

    def execute(self, action):
        """-> {"ok": bool, "text": str, "decision": "allow"|"deny"|"ask", "reason": str}"""
        decision = self.policy.check(action)
        if not decision.allowed:
            return {"ok": False, "text": "차단됨: " + decision.reason, "decision": decision.kind, "reason": decision.reason}
        tool = action.get("action")
        try:
            ok, text = getattr(self, "_" + tool)(action)
        except Exception as error:                                   # a tool bug or odd input must not end the run
            ok, text = False, "도구 오류: " + str(error)[:160]
        return {"ok": ok, "text": text, "decision": "allow", "reason": ""}

    # ---- reading -------------------------------------------------------------------------------------------
    def _plan(self, action):
        return True, "계획 기록됨"

    def _list(self, action):
        prefix = (action.get("path") or "").replace("\\", "/").strip("/")
        names = [f for f in self.files if not prefix or f == prefix or f.startswith(prefix + "/")]
        shown = names[:LIST_NAMES]
        return True, "\n".join(shown) + ("\n… 외 %d개" % (len(names) - len(shown)) if len(names) > len(shown) else "") if names else "(없음)"

    def _read(self, action):
        path = directmode.safe_path(self.worktree, action.get("path"))
        if path is None or not path.is_file():
            return False, "파일이 없습니다: %s" % str(action.get("path"))[:80]
        text, _, _ = directmode.read_text(path)
        lines = text.split("\n")
        try:
            start = max(1, int(action.get("start") or 1))
            end = min(len(lines), int(action.get("end") or start + READ_LINES - 1))
        except (TypeError, ValueError):
            return False, "start·end는 숫자로 적으세요"
        body = "\n".join("%d| %s" % (n, lines[n - 1]) for n in range(start, end + 1))
        note = "" if (start == 1 and end >= len(lines)) else "\n(%d~%d줄 / 전체 %d줄)" % (start, end, len(lines))
        return True, _clip(body, READ_CHARS) + note

    def _search(self, action):
        pattern = str(action.get("pattern") or "")
        if not pattern:
            return False, "pattern이 비었습니다"
        try:
            matcher = re.compile(pattern[3:] if pattern.startswith("re:") else re.escape(pattern), re.I)
        except re.error:
            return False, "정규식이 올바르지 않습니다"
        prefix = (action.get("path") or "").replace("\\", "/").strip("/")
        hits = []
        for rel in self.files:
            if prefix and not (rel == prefix or rel.startswith(prefix + "/")):
                continue
            path = directmode.safe_path(self.worktree, rel)
            if path is None or not path.is_file() or path.stat().st_size > SEARCH_FILE_CHARS:
                continue
            for number, line in enumerate(directmode.read_text(path)[0].split("\n"), 1):
                if matcher.search(line):
                    hits.append("%s:%d: %s" % (rel, number, line.strip()[:120]))
                    if len(hits) >= SEARCH_HITS:
                        return True, "\n".join(hits) + "\n… (더 있을 수 있음)"
        return True, "\n".join(hits) if hits else "(없음)"

    # ---- writing -------------------------------------------------------------------------------------------
    def _patch(self, action):
        rel = action.get("path")
        path = directmode.safe_path(self.worktree, rel)
        if path is None or not path.is_file():
            return False, "고칠 파일이 없습니다: %s" % str(rel)[:80]
        text, bom, crlf = directmode.read_text(path)
        blocks = action.get("blocks") or [(str(action.get("search") or ""), str(action.get("replace") or ""))]
        new_text, applied, failed = directmode.apply_blocks(text, blocks)
        if failed or not applied:
            head = "\n".join("%d| %s" % (n, line) for n, line in enumerate(text.split("\n")[:30], 1))
            return False, "SEARCH 글이 파일에서 정확히 한 번, 줄 그대로 나와야 합니다. 현재 파일 앞부분:\n" + _clip(head, 1200)
        if new_text == text:
            return False, "바뀐 내용이 없습니다"
        if len(text) > 200 and len(new_text.strip()) < len(text.strip()) * 0.2:
            return False, "파일 내용 대부분을 지우는 수정은 허용되지 않습니다"
        directmode.write_text(path, new_text, bom, crlf)
        self._mark(rel)
        return True, "%s 수정됨(%d개 블록)" % (rel, applied)

    def _create(self, action):
        rel = action.get("path")
        path = directmode.safe_path(self.worktree, rel)
        content = str(action.get("content") or "")
        if path is None or path.exists():
            return False, "만들 수 없는 경로이거나 이미 있습니다(있는 파일은 patch로 고치세요)"
        if not content.strip():
            return False, "내용이 비었습니다"
        path.parent.mkdir(parents=True, exist_ok=True)
        directmode.write_text(path, content if content.endswith("\n") else content + "\n", False, False)
        self.files.append(rel.replace("\\", "/"))
        self._mark(rel)
        return True, "%s 만들어짐" % rel

    def _mark(self, rel):
        rel = rel.replace("\\", "/")
        self.policy.written.add(rel)
        if rel not in self.changed:
            self.changed.append(rel)

    # ---- running -------------------------------------------------------------------------------------------
    def _run(self, action):
        command = str(action.get("command") or "").strip()
        try:
            argv = shlex.split(command, posix=True)
        except ValueError:
            return False, "명령을 해석하지 못했습니다"
        if not argv:
            return False, "명령이 비었습니다"
        argv[0] = sys.executable if argv[0] in ("python", "python3") else shutil.which(argv[0])
        if not argv[0]:
            return False, "이 PC에 실행 도구가 없습니다"
        try:
            proc = self.popen(argv, cwd=str(self.worktree), env=self.env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as error:
            return False, "시작하지 못했습니다: " + str(error)[:120]
        try:
            out, _ = proc.communicate(timeout=RUN_TIMEOUT)
            timed_out = False
        except subprocess.TimeoutExpired:
            if self.kill:
                self.kill(proc)
            out, timed_out = b"", True
        tail = (out or b"").decode("utf-8", "replace").strip()[-RUN_TAIL:]
        if timed_out:
            return False, "시간 초과(%d초)로 멈췄습니다" % RUN_TIMEOUT
        return proc.returncode == 0, "종료 코드 %s\n%s" % (proc.returncode, tail)
