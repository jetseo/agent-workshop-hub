"""Conversation storage: one JSON file per chat under ~/.agent-workshop/chats (private, atomic writes)."""
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

CHAT_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
MAX_MESSAGE = 100_000
MAX_MESSAGES = 2000
MAX_CHATS = 500
ROLES = ("user", "assistant")
DEFAULT_TITLE = "새 채팅"


_clock_lock = threading.Lock()
_last_stamp = 0.0


def _now():
    """Strictly increasing microsecond timestamps: two writes in the same instant must still sort in the
    order they happened (a fast machine can otherwise stamp them identically)."""
    global _last_stamp
    with _clock_lock:
        stamp = round(time.time(), 6)
        if stamp <= _last_stamp:
            stamp = round(_last_stamp + 0.000001, 6)
        _last_stamp = stamp
        return stamp


def default_dir():
    return Path.home() / ".agent-workshop" / "chats"


class ChatStore:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else default_dir()
        self._lock = threading.RLock()

    def _path(self, chat_id):
        if not isinstance(chat_id, str) or not CHAT_ID.fullmatch(chat_id):
            raise LookupError("Unknown chat")
        return self.folder / (chat_id + ".json")

    def _write(self, chat):
        self.folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self._path(chat["id"])
        tmp = path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(chat, handle, ensure_ascii=False)
        os.replace(tmp, path)

    def _read(self, chat_id):
        path = self._path(chat_id)
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise LookupError("Unknown chat") from None

    def create(self, model=""):
        with self._lock:
            if len(list(self.folder.glob("*.json"))) >= MAX_CHATS if self.folder.exists() else False:
                raise ValueError("Too many chats; delete some first")
            now = _now()
            chat = {"id": str(uuid.uuid4()), "title": DEFAULT_TITLE, "model": model, "created": now,
                    "updated": now, "messages": []}
            self._write(chat)
            return chat

    def list(self):
        with self._lock:
            items = []
            for path in self.folder.glob("*.json") if self.folder.exists() else []:
                try:
                    chat = json.loads(path.read_text(encoding="utf-8"))
                    items.append({k: chat[k] for k in ("id", "title", "model", "updated", "created")})
                except (OSError, ValueError, KeyError):
                    continue
            items.sort(key=lambda c: (c["updated"], c["created"]), reverse=True)
            return [{k: c[k] for k in ("id", "title", "model", "updated")} for c in items]

    def get(self, chat_id):
        with self._lock:
            return self._read(chat_id)

    def append(self, chat_id, role, content):
        if role not in ROLES or not isinstance(content, str) or not 1 <= len(content) <= MAX_MESSAGE:
            raise ValueError("Invalid message")
        with self._lock:
            chat = self._read(chat_id)
            if len(chat["messages"]) >= MAX_MESSAGES:
                raise ValueError("This chat is full; start a new one")
            chat["messages"].append({"role": role, "content": content, "time": _now()})
            chat["updated"] = _now()
            if role == "user" and chat["title"] == DEFAULT_TITLE:
                chat["title"] = " ".join(content.split())[:40] or DEFAULT_TITLE
            self._write(chat)
            return chat

    def pop_last_assistant(self, chat_id):
        with self._lock:
            chat = self._read(chat_id)
            if not chat["messages"] or chat["messages"][-1]["role"] != "assistant":
                return False
            chat["messages"].pop()
            self._write(chat)
            return True

    def update(self, chat_id, *, title=None, model=None):
        with self._lock:
            chat = self._read(chat_id)
            if title is not None:
                title = " ".join(str(title).split())
                if not 1 <= len(title) <= 80:
                    raise ValueError("Title must be 1-80 characters")
                chat["title"] = title
            if model is not None:
                if not isinstance(model, str) or len(model) > 300:
                    raise ValueError("Invalid model")
                chat["model"] = model
            self._write(chat)                      # renaming or switching model keeps the sidebar order
            return chat

    def delete(self, chat_id):
        with self._lock:
            path = self._path(chat_id)
            self._read(chat_id)
            path.unlink()
