import subprocess
import unittest
from pathlib import Path

from workshop import directmode
from workshop.codework import CodeRuns, CodeWorkError
from tests.test_codework import Base
from tests.test_directmode import Script, blocks

LOCALIZE = '{"files": ["calc.py"]}'
GOOD = blocks("    return a - b", "    return a + b")
BAD = blocks("    return a - b", "    return a * b")
BIGGER = blocks("    return a - b", "    x = a\n    y = b\n    z = x * y\n    return z")


class BestOfNTests(Base):
    def setUp(self):
        super().setUp()
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_unit.py").write_text(
            "import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "unit"], cwd=self.repo, check=True)

    def make(self, script):
        self.runs.shutdown()
        self.script = script
        self.runs = CodeRuns(self.providers, find_binary=lambda: None, worktree_base=self.dir / "wt",
                             direct_chat=lambda provider, model, messages, timeout, max_tokens, temperature=None: script(messages, max_tokens, temperature))
        self.addCleanup(self.runs.shutdown)

    def start(self, attempts, **kw):
        run = self.runs.start(str(self.repo), "add가 뺄셈을 해요. 고쳐줘", "loc/qwen2.5-coder:32b", mode="direct", attempts=attempts, **kw)
        return run["id"]

    def copies(self):
        base = self.dir / "wt" / "code"
        return sorted(p.name for p in base.iterdir()) if base.exists() else []

    def test_the_first_passing_attempt_wins_and_stops_the_rest(self):
        self.make(Script(localize=LOCALIZE, edits=[GOOD]))
        done = self.wait(self.start(3), "review")
        self.assertEqual([a["n"] for a in done["attempts_log"]], [1])                    # no second try was needed
        self.assertEqual(done["checks"]["verdict"], "passed")
        self.assertEqual(len(self.copies()), 1)

    def test_a_failing_first_try_is_replaced_by_a_passing_later_try_in_its_own_copy(self):
        self.make(Script(localize=LOCALIZE, edits=[BAD, BAD, GOOD]))                      # try 1: edit + repair both wrong; try 2 right
        done = self.wait(self.start(3), "review")
        log = {a["n"]: a for a in done["attempts_log"]}
        self.assertEqual((log[1]["verdict"], log[2]["verdict"]), ("failed", "passed"))
        self.assertTrue(log[2]["chosen"] and not log[1]["chosen"])
        self.assertIn("+    return a + b", done["patch"])
        self.assertEqual(done["checks"]["verdict"], "passed")
        self.assertEqual(len(self.copies()), 1)                                           # the losing copy was removed
        self.assertTrue(any("선택" in line or "2 결과" in line for line in done["activity"]))
        self.runs.apply(done["id"], True)                                                 # the normal apply flow works on the chosen copy
        self.assertIn("a + b", (self.repo / "calc.py").read_text(encoding="utf-8"))

    def test_when_nothing_passes_the_smaller_change_is_kept_and_the_failure_is_visible(self):
        self.make(Script(localize=LOCALIZE, edits=[BIGGER, blocks("    z = x * y\n    return z", "    z = x * y + 1\n    return z"),
                                                    BAD, blocks("    return a * b", "    return a * 2")]))
        done = self.wait(self.start(2), "review")
        log = {a["n"]: a for a in done["attempts_log"]}
        self.assertEqual((log[1]["verdict"], log[2]["verdict"]), ("failed", "failed"))
        self.assertTrue(log[2]["chosen"])                                                 # fewer changed lines
        self.assertEqual(done["checks"]["verdict"], "failed")                             # never hidden

    def test_later_attempts_use_a_higher_temperature_and_the_tests_are_shown_to_the_model(self):
        self.make(Script(localize=LOCALIZE, edits=[BAD, BAD, GOOD]))
        self.wait(self.start(2), "review")
        self.assertIsNone(self.script.temperatures[0])
        self.assertTrue(any(t is not None and t >= 0.7 for t in self.script.temperatures))
        edit_prompts = [user for system, user in self.script.calls if system.startswith("You edit")]
        self.assertTrue(all("Related tests (read-only" in p and "test_add" in p for p in edit_prompts))

    def test_a_single_attempt_has_no_attempt_log_and_nothing_changes(self):
        self.make(Script(localize=LOCALIZE, edits=[GOOD]))
        done = self.wait(self.start(1), "review")
        self.assertIsNone(done["attempts_log"])
        self.assertFalse(any("attempt" in line for line in done["activity"]))

    def test_bad_attempt_counts_and_unvalidated_best_of_n_are_refused(self):
        self.make(Script())
        for bad in (0, 6, -1, True, "3", 2.5):
            with self.assertRaises(CodeWorkError, msg=repr(bad)):
                self.runs.start(str(self.repo), "x", "loc/qwen2.5-coder:32b", mode="direct", attempts=bad)
        with self.assertRaises(CodeWorkError):
            self.runs.start(str(self.repo), "x", "loc/qwen2.5-coder:32b", mode="direct", attempts=3, validate=False)

    def test_cancel_stops_further_attempts_and_cleans_up(self):
        self.make(Script(localize=LOCALIZE, edits=[BAD, BAD]))
        run_id = self.start(3)
        self.runs.cancel(run_id)
        done = self.wait(run_id)
        self.assertEqual(done["status"], "cancelled")
        self.assertLessEqual(len(self.copies()), 1)


class ScoreTests(unittest.TestCase):
    def test_ranking_prefers_passing_then_untested_then_failing_then_broken(self):
        score = CodeRuns._trial_score
        passed = {"result": {"ok": True}, "verdict": "passed", "lines": 50, "n": 3}
        untested = {"result": {"ok": True}, "verdict": "none", "lines": 1, "n": 2}
        failed = {"result": {"ok": True}, "verdict": "failed", "lines": 1, "n": 1}
        broken = {"result": {"ok": False}, "verdict": "skipped", "lines": 0, "n": 1}
        self.assertEqual(sorted([broken, failed, untested, passed], key=score, reverse=True), [passed, untested, failed, broken])
        a = {"result": {"ok": True}, "verdict": "passed", "lines": 4, "n": 2}
        b = {"result": {"ok": True}, "verdict": "passed", "lines": 4, "n": 1}
        self.assertEqual(max([a, b], key=score), b)                                       # ties go to the earlier try

    def test_related_tests_are_bounded_and_skip_the_edited_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "tests" / "test_a.py").write_text("A" * 5000, encoding="utf-8")
            (root / "tests" / "test_b.py").write_text("B" * 5000, encoding="utf-8")
            (root / "tests" / "test_c.py").write_text("C" * 100, encoding="utf-8")
            text = directmode.related_tests(root, ["tests/test_a.py", "tests/test_b.py", "tests/test_c.py", "lib.py"], exclude=["tests/test_c.py"])
        self.assertIn("tests/test_a.py", text)
        self.assertNotIn("tests/test_b.py", text)                                         # would exceed the budget
        self.assertNotIn("test_c.py", text)
        self.assertLessEqual(len(text), 8200)


if __name__ == "__main__":
    unittest.main()
