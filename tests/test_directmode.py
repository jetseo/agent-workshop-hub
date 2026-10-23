import json
import tempfile
import unittest
from pathlib import Path

from workshop import directmode
from workshop.codework import CodeRuns, CodeWorkError
from tests.test_codework import Base

BUG = "def add(a, b):\n    return a - b\n"


def blocks(search, replace):
    return "<<<<<<< SEARCH\n%s\n=======\n%s\n>>>>>>> REPLACE" % (search, replace)


class Script:
    """A fake model: answers by the kind of question (the system prompt) from queues, and records every call."""

    def __init__(self, localize=None, edits=(), creates=()):
        self.localize = localize if localize is not None else '{"files": ["calc.py"], "new_files": []}'
        self.edits, self.creates = list(edits), list(creates)
        self.calls = []
        self.temperatures = []

    def __call__(self, messages, max_tokens=0, temperature=None):
        system, user = messages[0]["content"], messages[1]["content"]
        self.calls.append((system[:20], user))
        self.temperatures.append(temperature)
        if system.startswith("You are a careful"):
            return self.localize
        if system.startswith("You create"):
            return self.creates.pop(0) if self.creates else ""
        return self.edits.pop(0) if self.edits else ""


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "calc.py").write_text(BUG, encoding="utf-8", newline="\n")
        (self.dir / "notes.md").write_text("# n\n", encoding="utf-8")
        self.lines = []

    def emit(self, tool, ok, text):
        self.lines.append((tool, ok, text))

    def go(self, script, tests=(("passed", ""),), **kw):
        results = list(tests)
        return directmode.run(script, "add가 뺄셈을 해요. 고쳐줘", self.dir, ["calc.py", "notes.md"], "요약",
                              lambda: results.pop(0) if len(results) > 1 else results[0], self.emit, **kw)

    def test_localize_edit_validate_success(self):
        script = Script(edits=[blocks("    return a - b", "    return a + b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual((result["changed"], result["tests"], result["calls"]), (["calc.py"], "passed", 2))
        self.assertIn("a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))
        self.assertEqual([t for t, ok, _ in self.lines], ["localize", "edit", "test"])

    def test_a_block_that_does_not_match_gets_one_retry_with_the_reason(self):
        script = Script(edits=[blocks("    return a minus b", "x"), blocks("    return a - b", "    return a + b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["calls"], 3)
        self.assertIn("did not match exactly once", script.calls[-1][1])
        self.assertIn("a minus b", script.calls[-1][1])

    def test_two_failed_attempts_stop_with_a_clear_error_and_leave_the_file_alone(self):
        script = Script(edits=["sorry, no blocks", blocks("nope", "x")])
        result = self.go(script)
        self.assertFalse(result["ok"])
        self.assertIn("calc.py", result["error"])
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), BUG)

    def test_failing_tests_get_one_repair_round(self):
        script = Script(edits=[blocks("    return a - b", "    return a * b"), blocks("    return a * b", "    return a + b")])
        result = self.go(script, tests=[("failed", "assert 5 == 6"), ("passed", "")])
        self.assertTrue(result["ok"])
        self.assertEqual(result["tests"], "passed")
        self.assertIn("assert 5 == 6", script.calls[-1][1])                      # the model saw the failure
        self.assertIn("a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))
        self.assertIn("repair", [t for t, _, _ in self.lines])

    def test_still_failing_after_the_repair_is_reported_not_hidden(self):
        script = Script(edits=[blocks("    return a - b", "    return a * b"), blocks("    return a * b", "    return a * 2")])
        result = self.go(script, tests=[("failed", "x"), ("failed", "y")])
        self.assertTrue(result["ok"])                                              # the run finished; the verdict says what happened
        self.assertEqual(result["tests"], "failed")

    def test_validate_off_skips_the_tests(self):
        called = []
        script = Script(edits=[blocks("    return a - b", "    return a + b")])
        result = directmode.run(script, "t", self.dir, ["calc.py"], "", lambda: called.append(1) or ("passed", ""), self.emit, validate=False)
        self.assertTrue(result["ok"])
        self.assertEqual((called, result["tests"]), ([], "skipped"))

    def test_bom_and_crlf_are_preserved(self):
        (self.dir / "calc.py").write_bytes(b"\xef\xbb\xbf" + BUG.replace("\n", "\r\n").encode("utf-8"))
        script = Script(edits=[blocks("    return a - b", "    return a + b")])
        self.assertTrue(self.go(script)["ok"])
        raw = (self.dir / "calc.py").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(raw[3:], "def add(a, b):\r\n    return a + b\r\n".encode("utf-8"))

    def test_trailing_whitespace_differences_still_match(self):
        (self.dir / "calc.py").write_text("def add(a, b):   \n    return a - b  \n", encoding="utf-8", newline="\n")
        script = Script(edits=[blocks("def add(a, b):\n    return a - b", "def add(a, b):\n    return a + b")])
        self.assertTrue(self.go(script)["ok"])
        self.assertIn("a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))

    def test_ambiguous_search_text_is_refused(self):
        (self.dir / "calc.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
        script = Script(edits=[blocks("x = 1", "x = 2"), blocks("x = 1", "x = 3")])
        result = self.go(script)
        self.assertFalse(result["ok"])
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), "x = 1\nx = 1\n")

    def test_new_file_is_created_from_a_fenced_block_and_existing_paths_are_not_overwritten(self):
        script = Script(localize='{"files": [], "new_files": ["utils/helper.py"]}', creates=["Here:\n```python\nVALUE = 1\n```"])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.dir / "utils" / "helper.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        again = self.go(Script(localize='{"files": [], "new_files": ["notes.md"]}'))
        self.assertFalse(again["ok"] and again["changed"])
        self.assertEqual((self.dir / "notes.md").read_text(encoding="utf-8"), "# n\n")

    def test_a_new_file_named_under_files_is_still_a_new_file(self):
        script = Script(localize='{"files": ["test_calc.py"], "new_files": []}', creates=["```python\nimport calc\n```"])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.dir / "test_calc.py").read_text(encoding="utf-8"), "import calc\n")

    def test_an_empty_search_block_appends_to_the_end(self):
        script = Script(edits=[blocks("", "def mul(a, b):\n    return a * b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), BUG + "def mul(a, b):\n    return a * b\n")

    def test_a_whole_file_answer_is_accepted_when_there_are_no_blocks(self):
        script = Script(edits=["```python\ndef add(a, b):\n    return a + b\n```", "```python\ndef add(a, b):\n    return a + b\n```"])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertIn("a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))
        self.assertTrue(any("전체를 다시 씀" in text for _, _, text in self.lines))

    def test_a_change_that_leaves_the_file_unchanged_is_retried_not_counted(self):
        script = Script(edits=[blocks("    return a - b", "    return a - b"), blocks("    return a - b", "    return a + b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertIn("unchanged", script.calls[-1][1])
        self.assertIn("a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))

    def test_wiping_a_file_is_refused_even_when_the_blocks_apply(self):
        big = "line %d\n" * 1 % 1 + "".join("value_%d = %d\n" % (i, i) for i in range(60))
        (self.dir / "calc.py").write_text(big, encoding="utf-8")
        wipe = blocks(big.rstrip("\n"), "x = 1")
        result = self.go(Script(edits=[wipe, wipe]))
        self.assertFalse(result["ok"])
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), big)

    def test_a_destructive_request_is_refused_without_touching_anything(self):
        script = Script(localize='{"refuse": true, "reason": "deleting files"}')
        result = self.go(script)
        self.assertFalse(result["ok"])
        self.assertIn("위험한", result["error"])
        self.assertEqual(result["calls"], 1)
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), BUG)

    def test_the_model_cannot_name_paths_outside_the_listed_files(self):
        script = Script(localize='{"files": ["../evil.py", ".env", "/etc/passwd", "C:\\\\x.py"], "new_files": []}')
        result = self.go(script)
        self.assertFalse(result["ok"])
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["calc.py", "notes.md"])

    def test_safe_path_rules(self):
        for bad in ("../x", "a/../../x", "/abs", "C:/x", "", ".git/config", "src/.git/x", ".env", "keys/id_rsa", "a\x00b", "x" * 400):
            self.assertIsNone(directmode.safe_path(self.dir, bad), bad)
        self.assertIsNotNone(directmode.safe_path(self.dir, "src/new/file.py"))

    def test_garbage_or_a_failing_service_ends_in_an_error_never_an_exception(self):
        self.assertFalse(self.go(Script(localize="I think maybe the calculator?"))["ok"])

        def broken(messages, max_tokens=0):
            raise RuntimeError("Provider returned HTTP 429 (rate limit reached)")
        result = directmode.run(broken, "t", self.dir, ["calc.py"], "", lambda: ("passed", ""), self.emit)
        self.assertFalse(result["ok"])
        self.assertIn("429", result["error"])

    def test_cancel_stops_between_steps(self):
        script = Script(edits=[blocks("    return a - b", "    return a + b")])
        result = self.go(script, cancelled=lambda: True)
        self.assertFalse(result["ok"])
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), BUG)

    def test_prompt_size_is_bounded(self):
        (self.dir / "calc.py").write_text("x = 1\n" * 20000, encoding="utf-8")
        result = self.go(Script())
        self.assertFalse(result["ok"])
        self.assertIn("너무 커서", result["error"])


class TestFileProtectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "tests").mkdir()
        (self.dir / "calc.py").write_text(BUG, encoding="utf-8", newline="\n")
        self.test_text = "from calc import add\n# the yardstick\n"
        (self.dir / "tests" / "test_calc.py").write_text(self.test_text, encoding="utf-8", newline="\n")
        self.files = ["calc.py", "tests/test_calc.py"]

    def run_with(self, localize, edits):
        script = Script(localize=localize, edits=edits)
        lines = []
        result = directmode.run(script, "고쳐줘", self.dir, self.files, "", lambda: ("passed", ""), lambda *a: lines.append(a))
        return result, lines

    def test_an_existing_test_is_left_out_when_an_implementation_file_can_be_edited(self):
        result, lines = self.run_with('{"files": ["calc.py", "tests/test_calc.py"], "new_files": []}', [blocks("    return a - b", "    return a + b")])
        self.assertEqual(result["changed"], ["calc.py"])
        self.assertEqual((self.dir / "tests" / "test_calc.py").read_text(encoding="utf-8"), self.test_text)
        self.assertTrue(any("고치지 않음" in str(a) for a in lines))

    def test_a_task_that_only_names_a_test_file_may_still_edit_it(self):
        result, _ = self.run_with('{"files": ["tests/test_calc.py"], "new_files": []}', [blocks("# the yardstick", "# changed on request")])
        self.assertEqual(result["changed"], ["tests/test_calc.py"])


class OneFileAtATimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "calc.py").write_text(BUG, encoding="utf-8", newline="\n")
        (self.dir / "app.py").write_text("from calc import add\n\n\ndef run():\n    return add(1, 2)\n", encoding="utf-8", newline="\n")
        self.files = ["calc.py", "app.py"]

    def go(self, script):
        return directmode.run(script, "고쳐줘", self.dir, self.files, "", lambda: ("passed", ""), lambda *a: None)

    def test_a_later_file_sees_what_an_earlier_file_changed_and_new_files_come_first(self):
        script = Script(localize='{"files": ["calc.py", "app.py"], "new_files": ["helpers.py"]}',
                        creates=["```python\ndef double(x):\n    return x * 2\n```"],
                        edits=[blocks("    return a - b", "    return a + b"), blocks("    return add(1, 2)", "    return double(add(1, 2))")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed"], ["helpers.py", "calc.py", "app.py"])        # created first, then existing files in order
        last_edit_prompt = [c for c in script.calls if c[0].startswith("You edit")][-1][1]
        self.assertIn("def double", last_edit_prompt)                                  # the new file's content
        self.assertIn("return a + b", last_edit_prompt)                                # the earlier edit's diff

    def test_an_edit_that_breaks_python_syntax_is_retried(self):
        script = Script(localize='{"files": ["calc.py"], "new_files": []}',
                        edits=[blocks("    return a - b", "    return a +"), blocks("    return a - b", "    return a + b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertIn("return a + b", (self.dir / "calc.py").read_text(encoding="utf-8"))
        self.assertIn("invalid Python", script.calls[2][1])                            # the retry names the problem


    def test_the_new_file_prompt_shows_the_files_it_may_copy_from(self):
        script = Script(localize='{"files": ["calc.py"], "new_files": ["helpers.py"]}',
                        creates=["```python\ndef add(a, b):\n    return a + b\n```"], edits=[blocks("    return a - b", "    return a + b")])
        self.go(script)
        create_prompt = [c for c in script.calls if c[0].startswith("You create")][0][1]
        self.assertIn("Reference file calc.py", create_prompt)
        self.assertIn("return a - b", create_prompt)                                   # the original body, so a move keeps every character

    def test_edit_markers_are_never_accepted_as_file_content(self):
        garbled = "```\nSEARCH\ndef add(a, b):\n    return a - b\n=======\ndef add(a, b):\n    return a + b\n>>>>>>> REPLACE\n```"
        script = Script(localize='{"files": ["calc.py"], "new_files": []}', edits=[garbled, blocks("    return a - b", "    return a + b")])
        result = self.go(script)
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.dir / "calc.py").read_text(encoding="utf-8"), "def add(a, b):\n    return a + b\n")
        script2 = Script(localize='{"files": [], "new_files": ["x.py"]}', creates=["```\n<<<<<<< SEARCH\nx\n=======\ny\n>>>>>>> REPLACE\n```"])
        self.assertFalse(self.go(script2)["ok"])                                       # a created file made of edit markers is refused


class ContextHygieneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        (self.dir / "tests").mkdir()
        (self.dir / "calc.py").write_text(BUG, encoding="utf-8")
        (self.dir / "tests" / "test_calc.py").write_text("from calc import add\n# calc tests\n", encoding="utf-8")
        (self.dir / "tests" / "test_other.py").write_text("from other import thing\n# unrelated tests\n", encoding="utf-8")
        self.files = ["calc.py", "tests/test_calc.py", "tests/test_other.py"]

    def test_only_tests_that_import_the_edited_module_are_sent(self):
        everything = directmode.related_tests(self.dir, self.files)
        focused = directmode.related_tests(self.dir, self.files, targets=["calc.py"])
        self.assertIn("test_other.py", everything)
        self.assertIn("test_calc.py", focused)
        self.assertNotIn("test_other.py", focused)
        self.assertLess(len(focused), len(everything))

    def test_when_no_test_mentions_the_module_all_are_kept_as_a_fallback(self):
        text = directmode.related_tests(self.dir, self.files, targets=["unknown.py"])
        self.assertIn("test_calc.py", text)
        self.assertIn("test_other.py", text)

    def test_context_is_chosen_after_localization_and_failure_output_is_reduced(self):
        script = Script(edits=[blocks("    return a - b", "    return a * b"), blocks("    return a * b", "    return a + b")])
        seen = []

        def context_for(existing):
            seen.append(list(existing))
            return directmode.related_tests(self.dir, self.files, exclude=existing, targets=existing)
        noisy = "\n".join('  File "x.py", line %d, in test' % i for i in range(300)) + "\nAssertionError: 5 != 6"
        results = [("failed", noisy), ("passed", "")]
        result = directmode.run(script, "고쳐줘", self.dir, self.files, "", lambda: results.pop(0), lambda *a: None, context_for=context_for)
        self.assertTrue(result["ok"], result)
        self.assertEqual(seen, [["calc.py"]])
        edit_prompt = script.calls[1][1]
        self.assertIn("test_calc.py", edit_prompt)
        self.assertNotIn("test_other.py", edit_prompt)
        repair_prompt = script.calls[-1][1]
        self.assertIn("AssertionError: 5 != 6", repair_prompt)
        self.assertLess(len(repair_prompt), len(noisy))                                  # the 300-line trace was reduced


class RunsIntegrationTests(Base):
    def make(self, script):
        self.runs.shutdown()
        self.script = script
        self.runs = CodeRuns(self.providers, find_binary=lambda: None, worktree_base=self.dir / "wt",
                             direct_chat=lambda provider, model, messages, timeout, max_tokens, temperature=None: script(messages, max_tokens, temperature))
        self.addCleanup(self.runs.shutdown)

    def test_a_direct_run_produces_a_reviewable_patch_checks_report_and_needs_no_opencode(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_unit.py").write_text("import unittest\nfrom calc import add\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
        import subprocess
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "unit"], cwd=self.repo, check=True)
        self.make(Script(localize='{"files": ["calc.py"]}', edits=[blocks("    return a - b", "    return a + b")]))
        run = self.runs.start(str(self.repo), "고쳐줘", "loc/qwen2.5-coder:32b", mode="direct")
        self.assertEqual(run["mode"], "direct")
        done = self.wait(run["id"], "review")
        self.assertEqual([f["path"] for f in done["files"]], ["calc.py"])
        self.assertIn("+    return a + b", done["patch"])
        self.assertEqual(done["checks"]["verdict"], "passed")
        self.assertTrue(any("edit calc.py" in line for line in done["activity"]))
        self.assertIn("## 에이전트가 한 일", done["report"])
        metrics = json.loads((self.runs._path(run["id"]) / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(metrics["tool_calls"], 3)                                  # localize, edit, test
        self.runs.apply(run["id"], True)                                            # same review/apply flow as agent runs
        self.assertIn("a + b", (self.repo / "calc.py").read_text(encoding="utf-8"))

    def test_a_failed_pipeline_is_a_failed_run_with_the_reason(self):
        self.make(Script(localize="no idea"))
        run = self.runs.start(str(self.repo), "고쳐줘", "loc/qwen2.5-coder:32b", mode="direct")
        done = self.wait(run["id"], "failed")
        self.assertIn("정하지 못했습니다", done["error"])

    def test_auto_picks_the_strongest_local_or_free_model_and_never_opencode_or_paid(self):
        self.make(Script())
        ref = self.runs.auto_direct_model()
        self.assertIn(ref.split("/")[0], ("loc", "cloud"))
        self.assertNotEqual(ref.split("/")[0], "pay")
        for bad in ("opencode/x-free", "pay/gpt-x", "nope/zzz", "bad"):
            with self.assertRaises(CodeWorkError, msg=bad):
                self.runs.start(str(self.repo), "x", bad, mode="direct")

    def test_unknown_mode_is_refused(self):
        with self.assertRaises(CodeWorkError):
            self.runs.start(str(self.repo), "x", "loc/qwen2.5-coder:32b", mode="swarm")


if __name__ == "__main__":
    unittest.main()
