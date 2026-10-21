import unittest

from workshop import arbiter, modelpick
from workshop.router import route_task
from workshop.usage import tier_for

GEMINI_LIKE = [
    "models/gemini-2.5-flash", "models/gemini-2.5-pro", "models/gemini-2.5-flash-lite", "models/gemini-2.0-flash",
    "models/gemini-2.5-flash-preview-tts", "models/gemini-2.5-pro-preview-tts", "models/gemini-embedding-001",
    "models/text-embedding-004", "models/imagen-4.0-generate-001", "models/veo-3.0-generate", "models/gemini-live-2.5-flash",
    "models/gemini-flash-latest", "models/gemini-3-pro-preview", "models/gemma-4-31b-it", "models/gemma-4-26b-a4b-it", "models/aqa",
]


class ArbiterTests(unittest.TestCase):
    def kind(self, text, previous=None):
        return arbiter.classify(text, previous)

    def test_code_questions(self):
        self.assertEqual(self.kind("파이썬에서 리스트를 뒤집는 방법 알려줘")["type"], "code")
        trace = 'Traceback (most recent call last):\n  File "app.py", line 3\nValueError: bad\n이거 고쳐줘'
        self.assertEqual(self.kind(trace)["type"], "code")
        self.assertEqual(self.kind("```js\nconst a = 1\n```\n이 코드 왜 안 돼?")["type"], "code")

    def test_everyday_questions_stay_light(self):
        for text in ("안녕 오늘 기분 어때?", "맛있는 김치찌개 레시피 알려줘", "Translate this to English: 좋은 아침"):
            decision = self.kind(text)
            self.assertEqual((decision["type"], decision["level"]), ("chat", 1), text)

    def test_research_questions_are_heavy(self):
        decision = self.kind("전기차 시장의 최근 동향을 조사하고 주요 업체를 비교 분석해서 근거와 출처가 있는 보고서로 정리해줘")
        self.assertEqual((decision["type"], decision["level"]), ("research", 3))
        many = self.kind("1. 장단점 2. 비용 3. 위험 4. 대안을 각각 분석해줘")
        self.assertEqual(many["type"], "research")

    def test_size_raises_the_level(self):
        long_text = "자료를 읽고 정리해줘. " + "내용 " * 900
        self.assertEqual(self.kind(long_text)["level"], 3)
        self.assertEqual(self.kind("refactor the architecture of this distributed server")["level"], 3)

    def test_short_follow_up_keeps_the_conversation_kind_but_new_topics_do_not(self):
        self.assertEqual(self.kind("왜 그래?", "code")["type"], "code")
        self.assertEqual(self.kind("그럼 더 빠르게 할 수 없어?", "code")["type"], "code")
        self.assertEqual(self.kind("맛있는 김치찌개 레시피 알려줘", "research")["type"], "chat")      # a new topic
        self.assertEqual(self.kind("Translate this to English: 좋은 아침", "code")["type"], "chat")

    def test_bad_input_and_explanation(self):
        self.assertEqual(self.kind(None)["type"], "chat")
        self.assertIn("코드", arbiter.explain({"type": "code", "level": 2}, "m/x"))


class CurationTests(unittest.TestCase):
    def test_non_chat_models_and_prefixes_are_removed(self):
        models = modelpick.curate(GEMINI_LIKE)["models"]
        self.assertNotIn("gemini-embedding-001", models)
        for bad in ("tts", "embedding", "imagen", "veo", "live", "aqa"):
            self.assertFalse([m for m in models if bad in m], bad)
        self.assertTrue(all(not m.startswith("models/") for m in models))
        self.assertIn("gemini-2.5-pro", models)

    def test_recommendation_is_small_varied_and_stable(self):
        rec = modelpick.curate(GEMINI_LIKE)["recommended"]
        self.assertLessEqual(len(rec), modelpick.MAX_RECOMMENDED)
        self.assertIn("gemini-2.5-flash", rec)
        self.assertIn("gemini-2.5-pro", rec)                       # a strong model is offered next to the fast ones
        self.assertFalse([m for m in rec if "preview" in m or "latest" in m])

    def test_gemini_is_not_mistaken_for_a_mini_model(self):
        self.assertEqual(modelpick.capability("gemini-2.5-pro")["level"], 3)
        self.assertEqual(modelpick.capability("gemini-2.5-flash")["level"], 2)
        self.assertEqual(modelpick.capability("gemini-2.5-flash-lite")["level"], 1)

    def test_capability_levels_and_flags(self):
        self.assertEqual(modelpick.capability("qwen2.5-coder:7b"), {"level": 1, "coder": True, "reasoning": False})
        self.assertEqual(modelpick.capability("llama-3.3-70b-versatile")["level"], 3)
        self.assertEqual(modelpick.capability("llama-3.1-8b-instant")["level"], 1)
        self.assertTrue(modelpick.capability("deepseek-r1")["reasoning"])
        self.assertEqual(modelpick.capability("mystery")["level"], 2)

    def test_empty_and_junk_lists(self):
        self.assertEqual(modelpick.curate([])["recommended"], [])
        self.assertEqual(modelpick.curate([None, 5, "x"])["models"], ["x"])


class RouteTaskTests(unittest.TestCase):
    MODELS = ["ollama/qwen2.5-coder:7b", "mock/gemini-2.5-flash", "mock/gemini-2.5-pro", "mock/deepseek-r1", "mock/llama-3.1-8b-instant"]
    TIER = staticmethod(lambda ref: "local" if ref.startswith("ollama/") else "free")

    def pick(self, kind, level, **kw):
        return route_task({"type": kind, "level": level}, self.MODELS, tier_fn=self.TIER, **kw)

    def test_different_kinds_of_work_get_different_models(self):
        self.assertEqual(self.pick("code", 1)["model"], "ollama/qwen2.5-coder:7b")           # light code: the coder model
        self.assertEqual(self.pick("chat", 1)["model"], "mock/llama-3.1-8b-instant")          # everyday: small, fast
        self.assertIn(self.pick("research", 3)["model"], {"mock/gemini-2.5-pro", "mock/deepseek-r1"})   # deep research: a strong model

    def test_heavy_code_goes_to_a_stronger_model_than_light_code(self):
        self.assertEqual(self.pick("code", 3)["model"], "mock/gemini-2.5-pro")

    def test_cooling_down_models_are_skipped_and_the_best_remaining_is_used(self):
        stats = {"mock/deepseek-r1": {"runs": 2, "failures": 2, "streak_fail": 2, "last_ts": 1000}}
        self.assertEqual(self.pick("research", 3, stats=stats, now=1100)["model"], "mock/gemini-2.5-pro")
        stats["mock/gemini-2.5-pro"] = dict(stats["mock/deepseek-r1"])
        self.assertEqual(self.pick("research", 3, stats=stats, now=1100)["model"], "mock/gemini-2.5-flash")   # next best fit

    def test_nothing_usable_is_reported(self):
        decision = route_task({"type": "chat", "level": 1}, ["x/paid-model"], tier_fn=lambda ref: "paid")
        self.assertIsNone(decision["model"])

    def test_weak_pool_explains_itself(self):
        decision = route_task({"type": "research", "level": 3}, ["ollama/tiny-3b"], tier_fn=self.TIER)
        self.assertEqual(decision["model"], "ollama/tiny-3b")
        self.assertIn("strongest available", decision["reason"])


class ServiceFreeTierTests(unittest.TestCase):
    def test_free_all_services_count_as_free_but_never_unmarked_ones(self):
        self.assertEqual(tier_for({"data_policy": "cloud-may-train"}, "gemini-2.5-pro"), "other")
        self.assertEqual(tier_for({"data_policy": "cloud-may-train", "free_all": True}, "gemini-2.5-pro"), "free")
        self.assertEqual(tier_for({"data_policy": "cloud-may-train"}, "x:free"), "free")
        self.assertEqual(tier_for({"data_policy": "local"}, "anything"), "local")

    def test_free_match_marks_only_matching_names(self):
        entry = {"data_policy": "cloud-may-train", "free_match": ["flash"]}
        self.assertEqual(tier_for(entry, "GLM-4.7-Flash"), "free")
        self.assertEqual(tier_for(entry, "glm-5-plus"), "other")




class ContinuityTests(unittest.TestCase):
    def messages(self, n, size=3000):
        out = []
        for i in range(n):
            out.append({"role": "user", "content": "요청 %d: " % i + "가" * size})
            out.append({"role": "assistant", "content": "답변 %d: " % i + "나" * size})
        return out

    def test_short_chats_are_sent_whole_without_a_note(self):
        from workshop.chat_api import _history
        history = _history({"messages": self.messages(3)})
        self.assertEqual(len(history), 6)
        self.assertNotIn("system", [m["role"] for m in history])

    def test_long_chats_keep_the_original_goal_for_whichever_model_answers_next(self):
        from workshop.chat_api import _history, HISTORY_CHARS
        chat = {"messages": self.messages(12)}
        history = _history(chat)
        self.assertEqual(history[0]["role"], "system")
        self.assertIn("요청 0", history[0]["content"])                       # the very first request survives
        self.assertIn("앞선 대화 메모", history[0]["content"])
        self.assertEqual(history[-1], chat["messages"][-1])                    # recent turns are intact
        self.assertLessEqual(sum(len(m["content"]) for m in history[1:]), HISTORY_CHARS + 3500)
        self.assertLessEqual(len(history[0]["content"]), 1800)

    def test_note_lists_later_dropped_requests_but_stays_short(self):
        from workshop.chat_api import _earlier_note
        dropped = self.messages(30, size=50)
        note = _earlier_note(dropped)
        self.assertIn("요청 0", note)
        self.assertIn("요청 29", note)
        self.assertNotIn("요청 5:", note.split("그 뒤")[1])                  # only the last few are listed
        self.assertEqual(_earlier_note([]), "")
        self.assertEqual(_earlier_note([{"role": "assistant", "content": "x"}]), "")


class OnboardingSaveTests(unittest.TestCase):
    def run_save(self, service, model, extra, key="AIzaSyFakeKeyForTests1234"):
        import tempfile
        from pathlib import Path
        from workshop.providers import Providers
        from workshop import setup_page
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        providers = Providers(Path(d.name) / "providers.json")
        form = {"service": [service], "key": [key], "model": [model], "extra_models": [extra], "prompt": ["hi"],
                "consent": ["on"], "save": ["on"]}
        result = setup_page.run_test(providers, form, chat_fn=lambda *a, **k: "hello")
        return providers, result

    def test_recommended_models_are_saved_together_and_the_service_is_marked_free_tier(self):
        providers, result = self.run_save("gemini", "gemini-2.5-flash", "gemini-2.5-pro,gemini-2.5-flash-lite,bad name;rm -rf,gemini-2.5-pro")
        self.assertTrue(result["ok"] and result["saved"])
        entry = providers.get("gemini")
        self.assertCountEqual(entry["models"], ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"])
        self.assertEqual(entry["models"][0], "gemini-2.5-flash")                      # the tested one stays first
        self.assertTrue(entry["free_all"])
        self.assertEqual(tier_for(entry, "gemini-2.5-pro"), "free")

    def test_suffix_services_are_not_marked_all_free(self):
        providers, _ = self.run_save("openrouter", "some/model:free", "other/model")
        entry = providers.get("openrouter")
        self.assertFalse(entry.get("free_all"))
        self.assertEqual(tier_for(entry, "other/model"), "other")
        self.assertEqual(tier_for(entry, "some/model:free"), "free")

    def test_extra_models_are_limited_and_validated(self):
        from workshop.setup_page import _extra_models
        many = ",".join("m%d" % i for i in range(20))
        self.assertEqual(len(_extra_models({"extra_models": [many]}, "x")), 6)
        self.assertEqual(_extra_models({"extra_models": ["a,b;c,$(x),d e"]}, "a"), [])
        self.assertEqual(_extra_models({}, "a"), [])

if __name__ == "__main__":
    unittest.main()
