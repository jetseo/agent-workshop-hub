"""Policy engine for the agent loop's tools: one place that decides allow / deny / ask for every tool call.

The model never executes anything. It names an action, and this module judges it before `tools.ToolBox` runs it:
  - paths: only inside the work folder, never `.git` or secret-looking names, no absolute or `..` paths;
  - writes: a small number of files, bounded size, new files may not overwrite;
  - commands: never-list (cmdguard) is denied outright, allow-listed commands run, anything else is "ask"
    (recorded for the user, not executed). Text the model read (README, web, tool output) cannot widen these rules.
"""
from . import cmdguard, directmode

READ_TOOLS = ("list", "read", "search")
WRITE_TOOLS = ("patch", "create")


class Decision:
    __slots__ = ("kind", "reason")

    def __init__(self, kind, reason=""):
        self.kind, self.reason = kind, reason

    @property
    def allowed(self):
        return self.kind == "allow"


class Policy:
    def __init__(self, worktree, allowed_commands=(), max_files=8, max_write_chars=60_000):
        self.worktree = worktree
        self.allowed_commands = list(allowed_commands)
        self.max_files, self.max_write_chars = max_files, max_write_chars
        self.written = set()

    def check(self, action):
        tool = action.get("action")
        if tool in ("plan", "finish"):
            return Decision("allow")
        if tool in READ_TOOLS:
            path = action.get("path") or ""
            if path and directmode.safe_path(self.worktree, path) is None:
                return Decision("deny", "허용되지 않는 경로입니다(복사본 밖·.git·비밀 이름·절대 경로)")
            return Decision("allow")
        if tool in WRITE_TOOLS:
            path = action.get("path")
            if directmode.safe_path(self.worktree, path) is None:
                return Decision("deny", "허용되지 않는 경로입니다(복사본 밖·.git·비밀 이름·절대 경로)")
            if path not in self.written and len(self.written) >= self.max_files:
                return Decision("deny", "한 작업에서 바꿀 수 있는 파일은 %d개까지입니다" % self.max_files)
            target = directmode.safe_path(self.worktree, path)
            if tool == "patch" and directmode.is_test_path(path.replace("\\", "/")) and target.is_file():
                return Decision("deny", "기존 테스트 파일은 읽기 전용입니다(테스트를 고쳐 통과시키지 말고 구현을 고치세요)")
            size = len(str(action.get("content", ""))) + len(str(action.get("replace", "")))
            if size > self.max_write_chars:
                return Decision("deny", "한 번에 쓸 수 있는 양을 넘었습니다")
            return Decision("allow")
        if tool == "run":
            command = str(action.get("command") or "").strip()
            level, note = cmdguard.classify(command)
            if level == "never":
                return Decision("deny", "허용할 수 없는 명령입니다: " + note)
            if cmdguard.is_allowed(command, self.allowed_commands):
                return Decision("allow")
            return Decision("ask", "허용 목록에 없는 명령입니다(사용자 승인이 필요)")
        return Decision("deny", "알 수 없는 동작입니다: %s" % str(tool)[:30])
