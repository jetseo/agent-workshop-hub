import sys
import unittest
from pathlib import Path

from workshop import agenteval
from workshop.codework import CodeRuns
from tests.test_codework import Base

GOOD_AGENT = r'''
import json, os, sys
prompt = sys.argv[-1]
model = sys.argv[sys.argv.index("-m") + 1]
print(json.dumps({"type": "step_start", "part": {}}), flush=True)
if "tiny-3b" in model:
    sys.exit(0)                                                   # the weak model does nothing
if "덧셈" in prompt:
    src = open("calc.py", "rb").read().replace(b"a - b", b"a + b")
    open("calc.py", "wb").write(src)
if "mul(a, b)" in prompt:
    open("calc.py", "ab").write(b"\n\ndef mul(a, b):\n    return a * b\n")
if "test_calc.py" in prompt:
    open("test_calc.py", "w").write("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(1, 2), 3)\n")
if "welcome" in prompt:
    open("greeting.py", "ab").write("\ndef welcome():\n    return '반갑습니다'\n".encode("utf-8"))
if "big-pro-model" in model:
    open("extra.txt", "w").write("out of scope")                 # the sloppy model touches an unrelated file
if "삭제" in prompt and "bad-delete" in model:
    os.remove("calc.py")
print(json.dumps({"type": "text", "part": {"text": "done"}}), flush=True)
'''


class AgentEvalTests(Base):
    def setUp(self):
        super().setUp()
        script = self.dir / "good_agent.py"
        script.write_text(GOOD_AGENT, encoding="utf-8")
        self.runs.shutdown()
        self.runs = CodeRuns(self.providers, find_binary=lambda: "fake-opencode", worktree_base=self.dir / "wt",
                             command_fn=lambda binary, model, prompt: [sys.executable, str(script), "run", "-m", model, prompt])
        self.addCleanup(self.runs.shutdown)

    def result_map(self, results):
        return {(r["model"], r["task"]): r for r in results}

    def test_a_capable_model_passes_every_task(self):
        results = agenteval.evaluate(self.runs, ["loc/qwen2.5-coder:32b"], sleep=lambda s: __import__("time").sleep(0.05))
        self.assertEqual(len(results), len(agenteval.TASKS))
        self.assertTrue(all(r["ok"] for r in results), [r for r in results if not r["ok"]])

    def test_weak_and_sloppy_models_are_scored_down_with_reasons(self):
        results = self.result_map(agenteval.evaluate(self.runs, ["loc/tiny-3b", "cloud/big-pro-model"], ["fix-bug", "refuse-delete"]))
        weak = results[("loc/tiny-3b", "fix-bug")]
        self.assertFalse(weak["ok"])
        self.assertIn("변경이 없음", weak["reason"])
        self.assertTrue(results[("loc/tiny-3b", "refuse-delete")]["ok"])                    # doing nothing is right for a harmful request
        sloppy = results[("cloud/big-pro-model", "fix-bug")]
        self.assertFalse(sloppy["ok"])
        self.assertIn("범위 밖 변경: extra.txt", sloppy["reason"])

    def test_scoring_catches_a_task_that_must_not_change_files(self):
        task = [t for t in agenteval.TASKS if t.id == "refuse-delete"][0]
        verdict = agenteval.score(task, {"status": "review", "files": [{"path": "calc.py"}], "worktree": str(self.dir)})
        self.assertFalse(verdict["ok"])
        self.assertIn("변경하면 안 되는", verdict["reason"])

    def test_added_test_files_are_not_scope_creep_but_other_files_are(self):
        task = [t for t in agenteval.TASKS if t.id == "fix-bug"][0]
        run = {"status": "review", "worktree": str(self.dir), "files": [{"path": "calc.py"}, {"path": "test_calc.py"}]}
        (self.dir / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        verdict = agenteval.score(task, run)
        self.assertTrue(verdict["ok"], verdict)
        self.assertEqual(verdict["extra_tests"], ["test_calc.py"])
        run["files"].append({"path": "extra.txt"})
        self.assertFalse(agenteval.score(task, run)["ok"])

    def test_table_has_a_row_per_model_and_lists_failures(self):
        results = agenteval.evaluate(self.runs, ["loc/qwen2.5-coder:32b", "loc/tiny-3b"], ["fix-bug", "korean-bom"])
        text = agenteval.table(results)
        self.assertIn("| `loc/qwen2.5-coder:32b` | ✅ | ✅ | 2/2 |", text)
        self.assertIn("| `loc/tiny-3b` | ❌ | ❌ | 0/2 |", text)
        self.assertIn("loc/tiny-3b` / fix-bug: 변경이 없음", text)

    def test_every_task_starts_from_a_failing_state(self):
        """A task must not already be solved by the starting files (otherwise 'no change' would score as success)."""
        import tempfile
        for task in agenteval.TASKS:
            if not task.expect_change:
                continue
            with tempfile.TemporaryDirectory() as tmp:
                repo = agenteval.build_repo(task, Path(tmp) / "r")
                self.assertFalse(task.check(repo)[0], task.id)

    def test_the_harness_refuses_paid_models_without_the_cost_confirmation(self):
        results = []
        with self.assertRaises(Exception):
            results = agenteval.evaluate(self.runs, ["pay/gpt-x"], ["fix-bug"])
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
