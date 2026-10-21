import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from workshop import plan_board
from workshop.orchestrator import create_board

PLAN = {"tasks": [{"title": "Write the plan", "kind": "plan", "depends_on": []},
                  {"title": "Edit the code", "kind": "code", "depends_on": [0]},
                  {"title": "Check the result", "kind": "qa", "depends_on": [0, 1]}]}


class ConversionTests(unittest.TestCase):
    def test_dependencies_and_kinds_are_mapped(self):
        spec = plan_board.plan_to_spec(PLAN, request="Do X\nmore", step_model="opencode/a-free",
                                       code_model="opencode/b-free", repo="/r")
        steps = {s["id"]: s for s in spec["steps"]}
        self.assertEqual(steps["t2"]["depends_on"], ["t1"])
        self.assertEqual(steps["t3"]["depends_on"], ["t1", "t2"])
        self.assertEqual((steps["t2"]["kind"], steps["t2"]["model"], steps["t2"]["repo"]),
                         ("code", "opencode/b-free", "/r"))
        self.assertNotIn("kind", steps["t1"])
        self.assertEqual(spec["title"], "Do X")

    def test_code_task_requires_a_repo_and_bad_plans_are_rejected(self):
        with self.assertRaises(ValueError):
            plan_board.plan_to_spec(PLAN, request="x", step_model="m/x")
        with self.assertRaises(ValueError):
            plan_board.plan_to_spec({"tasks": []}, request="x", step_model="m/x")
        with self.assertRaises(ValueError):
            plan_board.plan_to_spec({"tasks": [{"title": "t", "kind": "plan", "depends_on": [0]}]},
                                    request="x", step_model="m/x")

    def test_generated_spec_loads_as_a_board(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / "repo"
            (repo / ".git").mkdir(parents=True)
            spec = plan_board.plan_to_spec(PLAN, request="x", step_model="opencode/a-free", repo=str(repo))
            board = create_board(Path(d) / "boards", spec)
            self.assertEqual(sorted(board.steps()), ["t1", "t2", "t3"])
            self.assertEqual(board.ready_steps(), ["t1"])


class PlannerReplyTests(unittest.TestCase):
    def test_json_is_extracted_from_fenced_chatter(self):
        reply = "Sure!\n```json\n" + json.dumps(PLAN) + "\n```\nHope that helps"
        self.assertEqual(plan_board.extract_json(reply), PLAN)
        for bad in ("no json here", "{not json}"):
            with self.assertRaises(ValueError):
                plan_board.extract_json(bad)

    def test_opencode_planner_uses_scratch_dir_and_validates(self):
        seen = {}
        def fake(prompt, ws, **kw):
            seen.update(prompt=prompt, ws=ws, model=kw["model"])
            return {"exit_code": 0, "stdout": "> build · m\n\n" + json.dumps(PLAN)}
        plan = plan_board.plan_with_opencode("Build X", "opencode/a-free", run=fake)
        self.assertEqual(plan, PLAN)
        self.assertIn("workshop-plan-", seen["ws"])
        self.assertIn("Build X", seen["prompt"])
        with self.assertRaises(ValueError):
            plan_board.plan_with_opencode("x", "m/x", run=lambda *a, **k: {"exit_code": 0, "stdout": '{"tasks": 5}'})
        with self.assertRaises(RuntimeError):
            plan_board.plan_with_opencode("x", "m/x", run=lambda *a, **k: {"exit_code": 1})


class CliTests(unittest.TestCase):
    def run_cli(self, argv):
        err = io.StringIO()
        with redirect_stderr(err):
            code = plan_board.main(argv)
        return code, err.getvalue()

    def test_plan_file_to_spec_file(self):
        with tempfile.TemporaryDirectory() as d:
            plan_path, out = Path(d) / "plan.json", Path(d) / "spec.json"
            plan_path.write_text(json.dumps({"tasks": PLAN["tasks"][::2][:1]}), encoding="utf-8")
            code, _ = self.run_cli(["Do X", "--plan", str(plan_path), "--step-model", "opencode/a-free",
                                    "--out", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8"))["steps"][0]["id"], "t1")

    def test_opencode_planner_gates(self):
        base = ["Do X", "--opencode-model", "opencode/a-free", "--step-model", "opencode/a-free"]
        with patch("workshop.plan_board.plan_with_opencode", return_value={"tasks": [PLAN["tasks"][0]]}) as planner:
            code, msg = self.run_cli(base)
            self.assertEqual(code, 1); self.assertIn("--allow-opencode", msg)
            code, msg = self.run_cli(base + ["--allow-opencode"])
            self.assertEqual(code, 1); self.assertIn("--allow-cloud --allow-may-train", msg)
            code, msg = self.run_cli(["Do X", "--opencode-model", "openai/gpt-5.4", "--step-model", "m/x",
                                      "--allow-opencode", "--allow-cloud", "--allow-may-train"])
            self.assertEqual(code, 1); self.assertIn("--allow-paid-models", msg)
            planner.assert_not_called()
            with tempfile.TemporaryDirectory() as d:
                code, _ = self.run_cli(base + ["--allow-opencode", "--allow-cloud", "--allow-may-train",
                                               "--out", str(Path(d) / "s.json")])
                self.assertEqual(code, 0)
                planner.assert_called_once()


if __name__ == "__main__":
    unittest.main()
