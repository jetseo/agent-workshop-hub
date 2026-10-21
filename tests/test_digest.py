import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop import digest
from workshop.digest import CHUNK_CHARS, MAX_COMBINED, build_prompt, split_chunks
from workshop.providers import Providers
from workshop.usage import UsageLedger


class SplitTests(unittest.TestCase):
    def test_pieces_rebuild_the_original_and_respect_size_and_line_breaks(self):
        samples = ["", "short", "a" * 20_000, "line\n" * 5_000, "한글 문장입니다.\n" * 2_000, "x" * 8_000, "x" * 8_001,
                   ("word " * 300 + "\n") * 40]
        for text in samples:
            pieces = split_chunks(text)
            self.assertEqual("".join(pieces), text)
            self.assertTrue(all(0 < len(p) <= CHUNK_CHARS for p in pieces))
        lines = split_chunks("line\n" * 5_000)
        self.assertTrue(all(p.endswith("\n") for p in lines))               # cut at line breaks, not mid-line
        self.assertEqual(split_chunks(""), [])

    def test_prompt_has_the_preservation_rules_the_text_and_the_focus(self):
        prompt = build_prompt("ERROR at app.py:42", 2, 5, focus="실패한 테스트")
        self.assertIn("조각 2/5", prompt)
        self.assertIn("ERROR at app.py:42", prompt)
        self.assertIn("파일 경로", prompt)
        self.assertIn("지어내지", prompt)
        self.assertIn("실패한 테스트", prompt)
        self.assertIn("전체", build_prompt("x", 1, 1))
        self.assertNotIn("특히", build_prompt("x", 1, 1))


class Harness:
    """Fake registry: every provider is local unless listed in `paid`/`cloud`."""

    def __init__(self, chat_fn, extra=(), cloud=(), ledger=None):
        self.calls, self.chat_fn_impl, self.extra, self.cloud, self.ledger = [], chat_fn, list(extra), set(cloud), ledger
        self.claims = []

    def resolve(self, ref):
        if not isinstance(ref, str) or "/" not in ref:
            raise ValueError("모델을 선택하세요.")
        name, _, model = ref.partition("/")
        policy = "cloud-may-train" if name in self.cloud else "local"
        return {"data_policy": policy, "base_url": "http://x"}, model

    def candidates(self, ref, enabled):
        return [ref] + (self.extra if enabled else [])

    def chat_fn(self, provider, model, messages, **kw):
        self.calls.append((model, messages[0]["content"], kw))
        return self.chat_fn_impl(model, messages[0]["content"])

    def run(self, **body):
        body.setdefault("model", "loc/m1")
        gen = digest.stream(resolve=self.resolve, candidates=self.candidates, chat_fn=self.chat_fn, ledger=self.ledger,
                            body=body, claim=lambda: self.claims.append("claim") or True,
                            release=lambda: self.claims.append("release"))
        return list(gen)


def names(events):
    return [e[0] for e in events]


class StreamTests(unittest.TestCase):
    def test_single_chunk(self):
        h = Harness(lambda m, p: "- 핵심 하나")
        events = h.run(text="로그 한 줄", focus="에러")
        self.assertEqual(names(events), ["meta", "progress", "done"])
        done = events[-1][1]
        self.assertEqual((done["digest"], done["requests"], done["passes"]), ("- 핵심 하나", 1, 1))
        self.assertIn("에러", h.calls[0][1])
        self.assertEqual(h.calls[0][2]["max_tokens"], digest.REPLY_TOKENS)
        self.assertEqual(h.claims, ["claim", "release"])

    def test_many_chunks_are_summarised_in_order_and_reduction_is_reported(self):
        text = "".join("line %d of a long log\n" % i for i in range(3_000))        # ~3 chunks
        h = Harness(lambda m, p: "- 요약 " + str(len(h.calls)))
        events = h.run(text=text)
        total = len(split_chunks(text))
        self.assertGreaterEqual(total, 3)
        self.assertEqual(names(events).count("progress"), total)
        done = events[-1][1]
        self.assertEqual(done["digest"], "\n\n".join("- 요약 %d" % i for i in range(1, total + 1)))
        self.assertGreater(done["reduction_pct"], 90)
        self.assertGreater(done["before_tokens"], done["after_tokens"])
        self.assertEqual(done["requests"], total)
        self.assertFalse(done["still_long"])

    def test_a_long_result_is_compressed_once_more_then_stops(self):
        text = "x\n" * 12_000                                                        # 24k chars -> 3 chunks
        h = Harness(lambda m, p: "- " + "y" * 7_000)                                # each reply stays big
        events = h.run(text=text)
        done = events[-1][1]
        self.assertEqual(done["passes"], digest.MAX_PASSES)                         # a bounded number of passes
        self.assertTrue(done["still_long"])                                         # and it says so
        first_pass = len(split_chunks(text))
        self.assertGreater(done["requests"], first_pass)
        self.assertLessEqual(done["requests"], first_pass + digest.MAX_CHUNKS)

    def test_failure_switches_to_the_next_model_and_stays_there(self):
        def impl(model, prompt):
            if model == "bad":
                raise RuntimeError("HTTP 429")
            return "- ok from " + model
        h = Harness(impl, extra=["loc/good"])
        events = h.run(text="a\n" * 12_000, model="loc/bad")
        self.assertEqual(names(events).count("notice"), 1)
        self.assertEqual([c[0] for c in h.calls].count("bad"), 1)                   # not retried for every chunk
        self.assertTrue(all(c[0] == "good" for c in h.calls[1:]))
        self.assertEqual(events[-1][1]["model"], "loc/good")

    def test_all_models_failing_and_the_single_model_message(self):
        h = Harness(lambda m, p: (_ for _ in ()).throw(RuntimeError("down: " + m)), extra=["loc/b"])
        events = h.run(text="x")
        self.assertEqual(events[-1][0], "error")
        self.assertIn("모든 모델이 실패", events[-1][1]["message"])
        self.assertIn("loc/m1", events[-1][1]["message"])
        self.assertIn("loc/b", events[-1][1]["message"])
        only = Harness(lambda m, p: (_ for _ in ()).throw(RuntimeError("just this")))
        self.assertEqual(only.run(text="x")[-1][1]["message"], "just this")
        self.assertEqual(only.claims[-1], "release")
        self.assertEqual(names(Harness(lambda m, p: "  ").run(text="x"))[-1], "error")   # an empty reply is a failure

    def test_fallback_can_be_off(self):
        h = Harness(lambda m, p: (_ for _ in ()).throw(RuntimeError("nope")), extra=["loc/b"])
        events = h.run(text="x", fallback=False)
        self.assertNotIn("notice", names(events))
        self.assertEqual([c[0] for c in h.calls], ["m1"])

    def test_validation_and_cost_confirmation(self):
        h = Harness(lambda m, p: "- ok", cloud=["cc"])
        for body in ({"text": ""}, {"text": "   "}, {"text": 5}, {"text": "x" * 300_001}, {"text": "x", "focus": "f" * 201}, {"text": "x", "model": "nomodel"}):
            with self.assertRaises(ValueError, msg=str(body)):
                h.run(**body)
        self.assertEqual(h.calls, [])
        with self.assertRaises(PermissionError):
            h.run(text="x", model="cc/pricey")                                       # may charge money: needs the checkbox
        with self.assertRaises(PermissionError):
            h.run(text="x", model="cc/pricey", confirm_cost="yes")                   # only a real True counts
        self.assertEqual(h.run(text="x", model="cc/pricey", confirm_cost=True)[-1][0], "done")
        self.assertEqual(h.run(text="x", model="cc/big-free")[-1][0], "done")         # free-looking: no confirmation needed
        self.assertEqual(len(h.calls), 2)

    def test_usage_is_booked_per_request_as_estimates(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = UsageLedger(Path(d) / "u.jsonl")
            h = Harness(lambda m, p: "- 요약", ledger=ledger, cloud=["cc"])
            events = h.run(text="a\n" * 12_000, model="cc/pricey", confirm_cost=True)
            records = ledger._records()
            self.assertEqual(len(records), events[-1][1]["requests"])
            self.assertTrue(all(r["source"] == "digest" and r["estimated"] and r["tier"] == "other" for r in records))
            self.assertEqual(sum(r["prompt"] + r["completion"] for r in records), events[-1][1]["spent_tokens"])
            class Broken:
                def record(self, **kw):
                    raise OSError("disk full")
            self.assertEqual(Harness(lambda m, p: "- ok", ledger=Broken()).run(text="x")[-1][0], "done")

    def test_only_one_at_a_time_and_the_claim_is_released_when_stopped(self):
        busy = digest.stream(resolve=Harness(lambda m, p: "-").resolve, candidates=lambda r, e: [r], chat_fn=lambda *a, **k: "-",
                             ledger=None, body={"text": "x", "model": "loc/m"}, claim=lambda: False, release=lambda: self.fail("must not release a claim it never got"))
        self.assertIn("이미 압축", list(busy)[0][1]["message"])
        h = Harness(lambda m, p: "- ok")
        gen = digest.stream(resolve=h.resolve, candidates=h.candidates, chat_fn=h.chat_fn, ledger=None,
                            body={"text": "a\n" * 12_000, "model": "loc/m"}, claim=lambda: h.claims.append("claim") or True,
                            release=lambda: h.claims.append("release"))
        next(gen); next(gen)                                                          # meta, then the first progress
        gen.close()                                                                   # the browser pressed stop
        self.assertEqual(h.claims, ["claim", "release"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for target, value in (("workshop.providers.default_path", self.dir / "providers.json"),
                              ("workshop.usage.default_path", self.dir / "usage.jsonl")):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        Providers().add("loc", base_url="http://127.0.0.1:1/v1", data_policy="local", models=["m"])
        Providers().add("cloud", base_url="https://c.example.com/v1", data_policy="cloud-may-train", consented=True, models=["x-free"])
        (self.dir / "big.log").write_text("ERROR a.py:1\n" * 3_000, encoding="utf-8")

    def run_cli(self, *argv, reply="- 요약본"):
        out, err = io.StringIO(), io.StringIO()
        with patch("workshop.digest.chat", return_value=reply), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = digest.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_digest_to_stdout_and_to_a_file_with_stats(self):
        code, out, err = self.run_cli(str(self.dir / "big.log"), "--model", "loc/m", "--focus", "errors")
        self.assertEqual(code, 0)
        self.assertIn("- 요약본", out)
        self.assertIn("감소", err)
        self.assertIn("대조", err)                                                    # the accuracy warning is always printed
        target = self.dir / "out.md"
        code, out, err = self.run_cli(str(self.dir / "big.log"), "--model", "loc/m", "--out", str(target))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("- 요약본", target.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(UsageLedger()._records()), 2)

    def test_refusals(self):
        (self.dir / ".env").write_text("KEY=1", encoding="utf-8")
        (self.dir / "auth.json").write_text("{}", encoding="utf-8")
        for argv in ([str(self.dir / ".env"), "--model", "loc/m"], [str(self.dir / "auth.json"), "--model", "loc/m"],
                     [str(self.dir / "missing.txt"), "--model", "loc/m"], [str(self.dir / "big.log"), "--model", "nope/m"],
                     [str(self.dir / "big.log"), "--model", "m"],
                     [str(self.dir / "big.log"), "--model", "cloud/x-free"]):               # cloud needs both consent flags
            code, out, err = self.run_cli(*argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(out, "")
        code, out, err = self.run_cli(str(self.dir / "big.log"), "--model", "cloud/x-free", "--allow-cloud", "--allow-may-train")
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
