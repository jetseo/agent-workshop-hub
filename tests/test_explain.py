import unittest

from workshop.explain import explain


class ExplainTests(unittest.TestCase):
    def check(self, raw, expected_word):
        text = explain(raw)
        self.assertIn(expected_word, text)
        self.assertTrue(text.endswith("(" + raw + ")"), "the raw cause must stay visible")

    def test_known_failures_get_a_cause_and_a_next_step(self):
        self.check("Provider returned HTTP 401 (token rejected)", "토큰이 거부")
        self.check("Provider returned HTTP 403 (access denied)", "접근이 거부")
        self.check("Provider returned HTTP 404 (model or URL not found)", "이름을 찾지 못")
        self.check("Provider returned HTTP 429 (rate limit reached)", "한도")
        self.check("Local rate limit: 5 requests/minute for this service (retry in ~30s)", "한도")
        self.check("Provider returned HTTP 302 (redirect refused: check the base URL)", "옮겨")
        self.check("Provider returned HTTP 503", "서비스 쪽 문제")
        self.check("Provider unreachable: [Errno 111] Connection refused", "연결이 거부")
        self.check("Provider unreachable: [WinError 10061] No connection could be made", "연결이 거부")
        self.check("Provider unreachable: timed out", "시간이 초과")
        self.check("Provider unreachable: [Errno -2] Name or service not known", "주소를 찾지 못")
        self.check("Provider unreachable: [SSL: CERTIFICATE_VERIFY_FAILED] bad", "SSL")
        self.check("Provider unreachable: something odd", "연결하지 못")
        self.check("Unexpected provider response format", "OpenAI 호환")

    def test_unknown_messages_pass_through_unchanged(self):
        self.assertEqual(explain("모델이 빈 응답을 돌려주었습니다."), "모델이 빈 응답을 돌려주었습니다.")
        self.assertEqual(explain("something else entirely"), "something else entirely")

    def test_key_is_never_added_and_messages_do_not_grow_unbounded(self):
        self.assertNotIn("sk-", explain("Provider returned HTTP 401 (token rejected)"))
        self.assertLess(len(explain("Provider returned HTTP 429 (rate limit reached)")), 300)


if __name__ == "__main__":
    unittest.main()
