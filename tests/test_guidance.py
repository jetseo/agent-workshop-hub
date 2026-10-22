import sys
import unittest
from pathlib import Path

from workshop import agenteval, guidance
from workshop.codework import CodeRuns
from tests.test_codework import Base

ECHO = r'''
import json, sys
open("prompt.txt", "w", encoding="utf-8").write(sys.argv[-1])
print(json.dumps({"type": "text", "part": {"text": "ok"}}), flush=True)
'''


class BuildTests(unittest.TestCase):
    def test_two_sizes_by_model_level_and_commands_are_inserted(self):
        short, size_short = guidance.build(1, [])
        full, size_full = guidance.build(3, [{"kind": "test", "command": "npm test"}])
        self.assertEqual((size_short, size_full), ("short", "full"))
        self.assertLess(len(short), len(full))
        self.assertIn("`npm test`", full)
        self.assertIn("테스트 명령(찾아서)", short)                               # nothing detected: tells the agent to look
        self.assertLess(len(full), 900)

    def test_build_command_is_used_when_there_is_no_test_command(self):
        text, _ = guidance.build(2, [{"kind": "build", "command": "make build"}])
        self.assertIn("`make build`", text)


class StartTests(Base):
    def setUp(self):
        super().setUp()
        script = self.dir / "echo_agent.py"
        script.write_text(ECHO, encoding="utf-8")
        self.runs.shutdown()
        self.on = True
        self.runs = CodeRuns(self.providers, find_binary=lambda: "fake-opencode", worktree_base=self.dir / "wt", use_guidance=lambda: self.on,
                             command_fn=lambda binary, model, prompt: [sys.executable, str(script), "run", "-m", model, prompt])
        self.addCleanup(self.runs.shutdown)

    def prompt_seen(self, **kw):
        run = self.runs.start(str(self.repo), "고쳐줘", "loc/qwen2.5-coder:32b", **kw)
        self.wait(run["id"])
        return (Path(self.runs._read(run["id"])["worktree"]) / "prompt.txt").read_text(encoding="utf-8"), self.runs.get(run["id"])

    def test_guidance_follows_the_setting_and_can_be_overridden_per_run(self):
        text, run = self.prompt_seen()
        self.assertIn("작업 지침", text)
        self.assertEqual(run["guidance"], "full")
        self.assertTrue(text.startswith("고쳐줘"))
        self.on = False
        text, run = self.prompt_seen()
        self.assertNotIn("작업 지침", text)
        self.assertEqual(run["guidance"], "off")
        text, run = self.prompt_seen(guidance_on=True)
        self.assertIn("작업 지침", text)

    def test_detected_commands_reach_the_guidance_and_the_report_says_so(self):
        text, run = self.prompt_seen()
        self.assertIn("python -m unittest discover", text)
        report = (self.runs._path(run["id"]) / "report.md").read_text(encoding="utf-8")
        self.assertIn("작업 지침: 전체 지침", report)


class EvalVariantTests(Base):
    def test_both_runs_each_task_twice_with_labelled_rows(self):
        script = self.dir / "echo_agent.py"
        script.write_text(ECHO, encoding="utf-8")
        self.runs.shutdown()
        self.runs = CodeRuns(self.providers, find_binary=lambda: "fake-opencode", worktree_base=self.dir / "wt",
                             command_fn=lambda binary, model, prompt: [sys.executable, str(script), "run", "-m", model, prompt])
        self.addCleanup(self.runs.shutdown)
        results = agenteval.evaluate(self.runs, ["loc/qwen2.5-coder:32b"], ["fix-bug"], guidance="both")
        self.assertEqual({r["model"] for r in results}, {"loc/qwen2.5-coder:32b [지침 없음]", "loc/qwen2.5-coder:32b [지침 있음]"})
        table = agenteval.table(results)
        self.assertIn("[지침 있음]", table)
        self.assertIn("[지침 없음]", table)


if __name__ == "__main__":
    unittest.main()
