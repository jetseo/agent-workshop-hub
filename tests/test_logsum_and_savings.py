import tempfile
import time
import unittest
from pathlib import Path

from workshop.logsum import reduce_log
from workshop.usage import UsageLedger, render_page, summary_with_savings


def noisy_log():
    lines = ["\x1b[32mstarting build\x1b[0m"]
    for i in range(400):
        lines.append("2026-10-21 10:%02d:%02d INFO fetched package %d from cache" % (i // 60, i % 60, i))
    lines += ["Traceback (most recent call last):", '  File "app.py", line 9, in <module>', "ValueError: bad config"]
    for i in range(300):
        lines.append("2026-10-21 11:00:%02d INFO retrying 0x%x" % (i % 60, 4096 + i))
    lines.append("build finished with errors")
    return "\n".join(lines)


class LogsumTests(unittest.TestCase):
    def test_repeated_lines_collapse_and_errors_survive(self):
        text = noisy_log()
        result = reduce_log(text)
        out, stats = result["text"], result["stats"]
        self.assertIn("ValueError: bad config", out)
        self.assertIn("Traceback", out)
        self.assertIn("(x400)", out)               # timestamps/numbers differ, shape is the same
        self.assertNotIn("\x1b[", out)
        self.assertLess(stats["tokens_after"], stats["tokens_before"] * 0.1)
        self.assertEqual(stats["lines_before"], len(text.split("\n")))

    def test_budget_is_respected_and_important_lines_win(self):
        lines = ["error in module " + "".join(chr(97 + (i // 26 ** k) % 26) for k in range(3)) for i in range(2000)]  # distinct shapes
        out = reduce_log("\n".join(lines), max_chars=1000)["text"]
        self.assertLessEqual(len(out), 1100)
        self.assertIn("omitted", out)

    def test_short_log_is_unchanged_apart_from_counts(self):
        out = reduce_log("one\ntwo\nthree")["text"]
        self.assertEqual(out, "one\ntwo\nthree")

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            reduce_log(None)
        self.assertEqual(reduce_log("")["stats"]["chars_after"], 0)


class SavingsPageTests(unittest.TestCase):
    def test_usage_page_shows_cache_and_free_savings(self):
        from workshop.cache import ResultCache
        with tempfile.TemporaryDirectory() as d:
            ledger = UsageLedger(Path(d) / "usage.jsonl")
            ledger.record(provider="p", model="m", tier="local", prompt_tokens=100, completion_tokens=50,
                          estimated=True, source="board")
            cache = ResultCache(Path(d) / "cache")
            cache.put("p/m", "question " * 20, "answer " * 40)
            cache.get("p/m", "question " * 20)
            summary = summary_with_savings(ledger)
            self.assertEqual(summary["cache"]["hits"], 1)
            page = render_page(summary)
            self.assertIn("절감", page)
            self.assertIn("캐시 재사용으로 아낀 토큰", page)
            self.assertIn(format(summary["cache"]["tokens_saved"], ","), page)

    def test_page_renders_without_cache_data(self):
        with tempfile.TemporaryDirectory() as d:
            page = render_page(UsageLedger(Path(d) / "u.jsonl").summary())
            self.assertIn("절감", page)


class DigestPrefilterTests(unittest.TestCase):
    def test_prefilter_shrinks_what_the_model_sees(self):
        from workshop import digest
        seen = []

        def fake_chat(provider, model, messages, max_tokens=None):
            seen.append(messages[0]["content"]); return "요약"
        events = list(digest.stream(
            resolve=lambda ref: ({"data_policy": "local"}, "m"), candidates=lambda ref, fb: [ref],
            chat_fn=fake_chat, ledger=None, body={"text": noisy_log(), "model": "loc/m", "prefilter": True}))
        done = dict(events)["done"]
        self.assertIn("ValueError: bad config", seen[0])
        self.assertLess(done["prefilter"]["tokens_after"], done["prefilter"]["tokens_before"])
        self.assertEqual(done["before_tokens"], done["prefilter"]["tokens_before"])  # measured against the original

    def test_prefilter_is_off_by_default(self):
        from workshop import digest
        events = list(digest.stream(
            resolve=lambda ref: ({"data_policy": "local"}, "m"), candidates=lambda ref, fb: [ref],
            chat_fn=lambda *a, **k: "요약", ledger=None, body={"text": "plain text", "model": "loc/m"}))
        self.assertIsNone(dict(events)["done"]["prefilter"])


if __name__ == "__main__":
    unittest.main()
