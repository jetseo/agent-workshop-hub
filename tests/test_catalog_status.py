import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import catalog_check
from workshop.chat_api import Context
from workshop.providers import Providers


ROWS = [{"id": "groq", "label": "Groq", "status": 401, "ok": True, "detail": "응답함(키 필요)"},
        {"id": "gone", "label": "Gone", "status": None, "ok": False, "detail": "응답 없음"}]


class CatalogStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "catalog-status.json"

    def test_status_is_saved_with_the_time_and_loaded_back(self):
        saved = catalog_check.save_status(self.path, ROWS, now=1_800_000_000.0)
        self.assertEqual(saved["checked_at"], 1_800_000_000.0)
        loaded = catalog_check.load_status(self.path)
        self.assertTrue(loaded["services"]["groq"]["ok"])
        self.assertFalse(loaded["services"]["gone"]["ok"])
        self.assertNotIn("limit", str(loaded).lower())                              # only reachability: free limits are never recorded here

    def test_a_missing_or_damaged_file_means_nothing_known(self):
        self.assertEqual(catalog_check.load_status(self.path), {"checked_at": 0, "services": {}})
        self.path.write_text("{broken", encoding="utf-8")
        self.assertEqual(catalog_check.load_status(self.path)["services"], {})

    def test_the_check_runs_in_the_background_and_a_second_request_does_not_start_another(self):
        from workshop import chat_api
        ctx = Context(Providers(Path(self.tmp.name) / "providers.json"))
        started, release = [], __import__("threading").Event()

        def slow_check():
            started.append(1)
            release.wait(5)
            return ROWS
        ctx.catalog_check_fn = slow_check                                           # no real network in tests
        self.assertEqual(chat_api._state(ctx)["catalog_status"]["services"], {})
        first = chat_api.dispatch(ctx, "POST", "/catalog/check", {})
        second = chat_api.dispatch(ctx, "POST", "/catalog/check", {})
        self.assertTrue(first.payload["started"] and not second.payload["started"])
        self.assertTrue(chat_api._state(ctx)["catalog_status"]["running"])
        release.set()
        for _ in range(100):
            if not ctx.catalog_busy.is_set():
                break
            time.sleep(0.05)
        self.assertEqual(len(started), 1)
        self.assertTrue(chat_api._state(ctx)["catalog_status"]["services"]["groq"]["ok"])


if __name__ == "__main__":
    unittest.main()
