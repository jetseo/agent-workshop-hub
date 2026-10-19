"""Shared Markdown board for multi-model tasks. State only; never calls a model.

Layout: <root>/<task-id>/state.json (source of truth), board.md (rendered view),
steps/<step-id>.md (each step writes only its own file), .history/ (board snapshots).
Only the orchestrator calls merge_step(), so parallel steps never write the same file.
"""
import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

STEP_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
STATUSES = ("pending", "running", "done", "failed")


def _atomic_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Board:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.state_path = self.folder / "state.json"

    @classmethod
    def create(cls, root, title, goal, constraints=""):
        if not isinstance(title, str) or not title.strip() or len(title) > 500:
            raise ValueError("Invalid title")
        board = cls(Path(root) / str(uuid.uuid4()))
        board._save({"title": title.strip(), "goal": goal, "constraints": constraints,
                     "steps": {}, "context": [], "revision": 0})
        return board

    def _load(self):
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _save(self, state):
        state["revision"] += 1
        history = self.folder / ".history"
        if self.state_path.exists():
            history.mkdir(parents=True, exist_ok=True)
            _atomic_write(history / (str(state["revision"] - 1).zfill(5) + ".md"),
                          (self.folder / "board.md").read_text(encoding="utf-8")
                          if (self.folder / "board.md").exists() else "")
        _atomic_write(self.state_path, json.dumps(state, ensure_ascii=False, indent=2))
        _atomic_write(self.folder / "board.md", self._render(state))

    def add_step(self, step_id, model, depends_on=(), prompt="", kind="text", workspace=None):
        state = self._load()
        if not STEP_ID.fullmatch(step_id or ""):
            raise ValueError("Invalid step id")
        if kind not in ("text", "code"):
            raise ValueError("Invalid step kind")
        if step_id in state["steps"]:
            raise ValueError("Duplicate step id")
        for dep in depends_on:
            if dep not in state["steps"]:
                raise ValueError("Unknown dependency: " + dep)
        state["steps"][step_id] = {"model": model, "depends_on": list(depends_on),
                                   "prompt": prompt, "status": "pending", "merged": False,
                                   "kind": kind, "workspace": workspace}
        self._save(state)

    def set_status(self, step_id, status):
        if status not in STATUSES:
            raise ValueError("Invalid status")
        state = self._load()
        state["steps"][step_id]["status"] = status
        self._save(state)

    def steps(self):
        return self._load()["steps"]

    def ready_steps(self):
        """Pending steps whose dependencies are all done and merged; may run in parallel."""
        steps = self._load()["steps"]
        return [sid for sid, s in steps.items() if s["status"] == "pending"
                and all(steps[d]["status"] == "done" and steps[d]["merged"] for d in s["depends_on"])]

    def write_step_output(self, step_id, text):
        """A step writes only its own file."""
        if step_id not in self._load()["steps"]:
            raise ValueError("Unknown step")
        _atomic_write(self.folder / "steps" / (step_id + ".md"), text)

    def merge_step(self, step_id):
        """Orchestrator-only: mark the step done and fold its output into the board."""
        state = self._load()
        step = state["steps"][step_id]
        if not (self.folder / "steps" / (step_id + ".md")).exists():
            raise ValueError("Step has no output to merge")
        step["status"], step["merged"] = "done", True
        self._save(state)

    def add_context(self, note, author="human"):
        """Append-only shared facts and decisions."""
        state = self._load()
        state["context"].append({"author": author, "note": note, "time": int(time.time())})
        self._save(state)

    def context_for(self, step_id, max_chars=8000):
        """Prompt material for one step: goal, constraints, shared notes, dependency outputs."""
        state = self._load()
        step = state["steps"][step_id]
        parts = ["# Goal\n" + state["goal"]]
        if state["constraints"]:
            parts.append("# Constraints\n" + state["constraints"])
        if state["context"]:
            parts.append("# Shared context\n" + "\n".join("- " + c["note"] for c in state["context"]))
        for dep in step["depends_on"]:
            parts.append("# Output of " + dep + "\n" +
                         (self.folder / "steps" / (dep + ".md")).read_text(encoding="utf-8"))
        parts.append("# Your task\n" + step["prompt"])
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            # Keep the task and the newest material; drop from the front of the middle.
            tail = parts[-1]
            body = "\n\n".join(parts[:-1])[-(max_chars - len(tail) - 40):]
            text = "[...truncated...]\n" + body + "\n\n" + tail
        return text

    def _render(self, state):
        lines = ["# " + state["title"], "", "## 목표 / Goal", state["goal"], ""]
        if state["constraints"]:
            lines += ["## 제약 / Constraints", state["constraints"], ""]
        lines += ["## 계획 / Steps", ""]
        for sid, s in state["steps"].items():
            deps = ", ".join(s["depends_on"]) or "-"
            lines.append("- `" + sid + "` [" + s["status"] + "] " + s.get("kind", "text") + " model=" + str(s["model"]) + " depends_on=" + deps)
        lines += ["", "## 공유 컨텍스트 / Shared context"]
        lines += ["- (" + c["author"] + ") " + c["note"] for c in state["context"]] or ["(none)"]
        lines += ["", "## 단계별 산출물 / Step outputs"]
        for sid, s in state["steps"].items():
            if s["merged"]:
                lines += ["", "### " + sid, (self.folder / "steps" / (sid + ".md")).read_text(encoding="utf-8")]
        return "\n".join(lines) + "\n"
