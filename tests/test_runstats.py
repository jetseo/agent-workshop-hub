import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.runstats import RunStats


class CodeRunRecordingTests(unittest.TestCase):
    def test_code_outcomes_are_kept_apart_from_chat_routing_stats(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            stats = RunStats(Path(tmp) / "runstats.jsonl")
            stats.record("a/m", ok=True, seconds=1)
            stats.record("a/m", ok=False, seconds=9, kind="code:direct", tokens=1234)
            self.assertEqual(stats.load()["a/m"]["runs"], 1)
            self.assertEqual(stats.load()["a/m"]["failures"], 0)
            self.assertEqual(stats.load(kind="code")["a/m"]["failures"], 1)


if __name__ == "__main__":
    unittest.main()
