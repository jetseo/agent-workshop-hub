import json
import time
import unittest

from workshop import codereview
from workshop.codework import CodeWorkError
from tests.test_codework import Base


class ParseTests(unittest.TestCase):
    def test_reads_json_even_with_chatter_and_masks_secrets(self):
        text = 'Sure!\n{"verdict":"caution","summary":"키 sk-abcdefgh12345678 가 보임","issues":[{"severity":"high","where":"a.py","text":"x"}],"missing":["테스트"]}\nbye'
        result = codereview.parse(text)
        self.assertEqual(result["verdict"], "caution")
        self.assertNotIn("sk-abcdefgh", result["summary"])
        self.assertEqual(result["issues"][0]["severity"], "high")
        self.assertEqual(result["missing"], ["테스트"])

    def test_unreadable_answers_become_unknown_not_ok(self):
        for text in ("괜찮아 보여요", "{broken", '{"verdict":"great"}', ""):
            self.assertEqual(codereview.parse(text)["verdict"], "unknown", text)

    def test_messages_hold_request_facts_and_a_bounded_diff(self):
        run = {"prompt": "고쳐줘", "stat": "1개 파일"}
        messages = codereview.build_messages(run, "x" * 100_000, {"verdict": "failed", "items": [{"kind": "test", "command": "make test", "status": "failed"}]})
        body = messages[1]["content"]
        self.assertIn("고쳐줘", body)
        self.assertIn("make test=failed", body)
        self.assertLess(len(body), codereview.MAX_DIFF_CHARS + 600)

    def test_review_passes_the_answer_through_parse(self):
        answer = codereview.review({}, "m", {"prompt": "p"}, "diff", None, chat_fn=lambda *a, **k: json.dumps({"verdict": "ok", "summary": "좋음"}))
        self.assertEqual((answer["verdict"], answer["summary"]), ("ok", "좋음"))


class RunReviewTests(Base):
    def setUp(self):
        super().setUp()
        self.seen = []

        def fake(provider, model, run, patch_text, check):
            self.seen.append((model, patch_text))
            if "boom" in model:
                raise RuntimeError("service down sk-abcdefgh12345678")
            return {"verdict": "caution", "summary": "범위 확인 필요", "issues": [], "missing": []}
        self.runs.review_fn = fake

    def wait_review(self, run_id):
        deadline = time.time() + 20
        while time.time() < deadline:
            review = self.runs.get(run_id).get("review") or {}
            if review.get("state") in ("done", "failed"):
                return review
            time.sleep(0.05)
        self.fail("review did not finish")

    def test_auto_picks_a_different_local_or_free_model_and_records_it(self):
        run_id = self.wait(self.start("FIX 고쳐줘"), "review")["id"]
        self.runs.review(run_id)
        review = self.wait_review(run_id)
        self.assertEqual(review["state"], "done")
        self.assertNotEqual(review["model"], "loc/qwen2.5-coder:32b")
        self.assertNotIn(review["model"].split("/")[0], ("pay", "opencode"))                        # never paid, never OpenCode's own
        self.assertIn("a + b", self.seen[0][1])                                                      # the reviewer saw the diff
        report = (self.runs._path(run_id) / "report.md").read_text(encoding="utf-8")
        self.assertIn("코드 리뷰(수정과 다른 모델", report)
        self.assertIn("주의", report)
        self.assertEqual(self.runs.get(run_id)["status"], "review")                                   # a review never changes the state

    def test_the_writer_paid_and_unknown_models_cannot_review(self):
        run_id = self.wait(self.start("FIX"), "review")["id"]
        for ref in ("loc/qwen2.5-coder:32b", "pay/gpt-x", "opencode/x-free", "nope/zzz", "bad"):
            with self.assertRaises(CodeWorkError, msg=ref):
                self.runs.review(run_id, ref)

    def test_a_failing_service_is_reported_with_secrets_masked(self):
        self.providers.add("boomsvc", base_url="http://127.0.0.1:9/v1", data_policy="local", models=["boom-7b"])
        run_id = self.wait(self.start("FIX"), "review")["id"]
        self.runs.review(run_id, "boomsvc/boom-7b")
        review = self.wait_review(run_id)
        self.assertEqual(review["state"], "failed")
        self.assertNotIn("sk-abcdefgh", review["error"])

    def test_nothing_to_review_without_a_change(self):
        run_id = self.wait(self.start("hello"), "nochange")["id"]
        with self.assertRaises(CodeWorkError):
            self.runs.review(run_id)


if __name__ == "__main__":
    unittest.main()
