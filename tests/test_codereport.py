import json
import tempfile
import unittest
from pathlib import Path

from workshop import codereport
from tests.test_codework import Base


class ReadEventsTests(unittest.TestCase):
    def test_counts_tools_and_sums_tokens_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            events = [{"type": "tool_use", "part": {"callID": "1", "tool": "edit", "state": {"status": "completed"}}},
                      {"type": "tool_use", "part": {"callID": "2", "tool": "bash", "state": {"status": "error"}}},
                      {"type": "tool_use", "part": {"callID": "2", "tool": "bash", "state": {"status": "error"}}},
                      {"type": "step_finish", "part": {"tokens": {"input": 100, "output": 20}}},
                      {"type": "step_finish", "part": {"tokens": {"input": 50, "output": 5}}}]
            path.write_text("\n".join(json.dumps(e) for e in events) + "\nnot json\n", encoding="utf-8")
            result = codereport.read_events(path)
        self.assertEqual((result["tool_calls"], result["tool_errors"], result["steps"]), (2, 1, 2))
        self.assertEqual(result["tokens"], {"input": 150, "output": 25})

    def test_missing_events_or_tokens_are_reported_as_unknown(self):
        self.assertIsNone(codereport.read_events(Path("nope.jsonl"))["tokens"])

    def test_secrets_are_masked(self):
        text = codereport.mask("key sk-abcdefgh12345678 and api_key=hunter2hunter2 and Bearer abcdefghijklmnop")
        for leaked in ("sk-abcdefgh", "hunter2", "abcdefghijklmnop"):
            self.assertNotIn(leaked, text)


class RunReportTests(Base):
    def test_report_and_metrics_are_written_and_follow_the_run(self):
        run_id = self.wait(self.start("FIX 키 sk-abcdefgh12345678 로 고쳐줘"), "review")["id"]
        directory = self.runs._path(run_id)
        report = (directory / "report.md").read_text(encoding="utf-8")
        for section in ("## 목표", "## 최종 판정", "## 모델·비용", "## 프로젝트 요약", "## 에이전트가 한 일", "## 바뀐 파일", "## 빌드·테스트·리뷰"):
            self.assertIn(section, report)
        self.assertIn("calc.py", report)
        self.assertNotIn("sk-abcdefgh", report)
        self.assertIn("기록 없음", report)                                                   # build/test not claimed
        metrics = json.loads((directory / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual((metrics["status"], metrics["files_changed"], metrics["tool_calls"]), ("review", 1, 1))
        self.assertIsNone(metrics["tokens"])
        self.assertIn("## 최종 판정", self.runs.get(run_id)["report"])
        self.runs.apply(run_id, True)
        self.assertIn("적용됨", (directory / "report.md").read_text(encoding="utf-8"))
        self.runs.undo(run_id, True)
        self.assertIn("되돌림", (directory / "report.md").read_text(encoding="utf-8"))
        self.assertTrue(json.loads((directory / "metrics.json").read_text(encoding="utf-8"))["undone"])

    def test_failed_run_has_a_report_with_the_reason(self):
        run_id = self.wait(self.start("FAIL"), "failed")["id"]
        report = (self.runs._path(run_id) / "report.md").read_text(encoding="utf-8")
        self.assertIn("실패", report)
        self.assertIn("종료 코드 3", report)


if __name__ == "__main__":
    unittest.main()
