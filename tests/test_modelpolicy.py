import tempfile
import unittest
from pathlib import Path
from workshop.modelpolicy import classify, parse_models_output
from workshop.orchestrator import create_board, run_board
from workshop.providers import Providers

SAMPLE = """opencode/glm-5.3-flash-free
opencode/big-pickle
opencode/gpt-5.5
openai/gpt-5.4
opencode/nemotron-3-ultra-free
ollama/qwen2.5-coder:7b
"""


class PolicyTests(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify("opencode/glm-5.3-flash-free"), "free")
        self.assertEqual(classify("ollama/qwen2.5-coder:7b"), "local")
        for paid in ("opencode/gpt-5.5", "openai/gpt-5.4", "opencode/big-pickle", "x"):
            self.assertEqual(classify(paid), "paid")

    def test_parse(self):
        g = parse_models_output(SAMPLE)
        self.assertEqual(len(g["free"]), 2)
        self.assertEqual(len(g["local"]), 1)
        self.assertEqual(len(g["paid"]), 3)

    def test_orchestrator_blocks_paid_code_models_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def code(prompt, ws, **kw):
                calls.append(kw["model"]); return {"exit_code": 0, "stdout": "ok"}
            def run(model, **flags):
                board = create_board(Path(d) / "b", {"title": "T", "goal": "G", "steps": [
                    {"id": "c", "kind": "code", "model": model, "workspace": d}]})
                return run_board(board, Providers(Path(d) / "p.json"), allow_opencode=True,
                                 approved_code=["c"], code_fn=code, **flags)
            self.assertIn("--allow-paid-models", run("openai/gpt-5.4")["failed"]["c"])
            self.assertEqual(calls, [])
            self.assertEqual(run("opencode/glm-5.3-flash-free")["done"], ["c"])
            self.assertEqual(run("openai/gpt-5.4", allow_paid=True)["done"], ["c"])


if __name__ == "__main__":
    unittest.main()
