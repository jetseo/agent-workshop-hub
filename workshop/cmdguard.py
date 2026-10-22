"""Shell command rules for code work: classify a blocked command and keep per-project "always allow" rules.

The agent runs with a deny-by-default shell. A command it tried that is not on the allow list is shown to the user,
who may allow it once, allow it for this project, or leave it blocked. Some commands are never offered for approval
and the risky kinds (delete, network, install, permission change) can only be allowed once, never for good.
"""
import json
import os
import re
import threading
from pathlib import Path

MAX_RULES_PER_PROJECT = 50
MAX_COMMAND = 300

# (pattern, reason): never offered for approval
NEVER = [
    (r"\brm\s+(-\w+\s+)*-\w*[rf]\w*\s+(/|~|\*|\.\.|\$HOME|%\w+%)", "루트·홈·상위 폴더를 지우는 명령"),
    (r"\b(rd|rmdir)\s+/s\b.*([a-z]:\\?\s*$|\\\s*$)", "폴더 전체 삭제"),
    (r"\bformat\s+[a-z]:", "디스크 포맷"), (r"\bmkfs\b|\bdiskpart\b|\bdd\s+if=", "디스크를 직접 쓰는 명령"),
    (r"\bgit\s+push\b", "원격 저장소로 올리는 명령(푸시는 사용자가 직접)"),
    (r"\bsudo\b|\brunas\b", "관리자 권한 요청"),
    (r"\|\s*(sh|bash|zsh|powershell|pwsh|cmd)\b", "받은 내용을 바로 실행하는 파이프"),
    (r"-enc(odedcommand)?\b", "인코딩된 PowerShell 명령"),
    (r"\bshutdown\b|\breboot\b|\breg\s+(add|delete)\b", "시스템 종료·레지스트리 변경"),
    (r":\(\)\s*\{", "포크 폭탄"),
]
# (pattern, kind): may be allowed once, never for good
ONCE = [
    (r"\b(rm|del|erase|rmdir|rd)\b|remove-item|\bgit\s+clean\b|\bgit\s+reset\s+--hard\b", "삭제"),
    (r"\b(curl|wget|ssh|scp|sftp|nc|ncat|telnet|ftp)\b|invoke-webrequest|invoke-restmethod|\biwr\b|\birm\b", "네트워크"),
    (r"\b(npm|pnpm|yarn|bun)\s+(i|install|add|ci)\b|\bpip3?\s+install\b|\bapt(-get)?\b|\bwinget\b|\bchoco\b|\bcargo\s+install\b|\bgo\s+(get|install)\b|\bdotnet\s+(add|tool)\b|\bnpx\b", "패키지 설치"),
    (r"\b(chmod|chown|icacls|takeown|attrib|setx)\b", "권한·환경 변경"),
]
NEVER_RE = [(re.compile(p, re.I), reason) for p, reason in NEVER]
ONCE_RE = [(re.compile(p, re.I), kind) for p, kind in ONCE]


def classify(command):
    """-> (level, note). level: "never" (no approval offered), "once" (this time only) or "normal" (once or for the project)."""
    text = (command or "").strip()
    if not text or len(text) > MAX_COMMAND or "\n" in text or "\r" in text:
        return "never", "비어 있거나 너무 길거나 여러 줄인 명령"
    for pattern, reason in NEVER_RE:
        if pattern.search(text):
            return "never", reason
    if "*" in text:
        return "never", "와일드카드(*)가 든 명령은 허용 목록에 넣을 수 없음"
    for pattern, kind in ONCE_RE:
        if pattern.search(text):
            return "once", kind + " 명령은 이번만 허용할 수 있고, 항상 허용은 제공하지 않습니다"
    return "normal", ""


def wildcard_match(pattern, command):
    """OpenCode-style permission pattern: `*` matches anything, everything else is literal."""
    regex = ".*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(regex, command.strip(), flags=re.S) is not None


def is_allowed(command, patterns):
    return any(wildcard_match(pattern, command) for pattern in patterns)


def find_blocked(events_path, allowed, known=()):
    """Shell commands the agent tried that failed and are not on the allow list (so they were blocked by the deny-by-default rule)."""
    found, seen = [], set(known)
    path = Path(events_path)
    if not path.exists():
        return found
    with open(path, "rb") as handle:
        for raw in handle.read(8_000_000).splitlines():
            try:
                event = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                continue
            part = event.get("part") or {}
            state = part.get("state") or {}
            if event.get("type") != "tool_use" or part.get("tool") != "bash" or state.get("status") != "error":
                continue
            command = str((state.get("input") or {}).get("command") or "").strip()
            if command and command not in seen and not is_allowed(command, allowed):
                seen.add(command)
                found.append(command)
    return found


class ProjectRules:
    """Per-project "always allow" commands, stored beside the other JetAI data (not in the user's repository)."""

    def __init__(self, path):
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, repo):
        with self._lock:
            rules = self._load().get(str(repo), [])
        return [r for r in rules if isinstance(r, str)]

    def _save(self, data):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.path)
                return
            except PermissionError:
                if attempt == 4:
                    raise

    def add(self, repo, command):
        level, note = classify(command)
        if level != "normal":
            raise ValueError(note or "항상 허용할 수 없는 명령입니다.")
        with self._lock:
            data = self._load()
            rules = [r for r in data.get(str(repo), []) if isinstance(r, str)]
            if command not in rules:
                if len(rules) >= MAX_RULES_PER_PROJECT:
                    raise ValueError("이 프로젝트의 허용 명령이 너무 많습니다. 목록을 정리하세요.")
                rules.append(command)
            data[str(repo)] = rules
            self._save(data)

    def remove(self, repo, command):
        with self._lock:
            data = self._load()
            data[str(repo)] = [r for r in data.get(str(repo), []) if r != command]
            self._save(data)
