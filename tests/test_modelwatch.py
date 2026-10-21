import json
import tempfile
import unittest
from pathlib import Path

from workshop import modelinfo, modelpick
from workshop.modelinfo import ModelInfo, normalise
from workshop.modelwatch import ModelWatch
from workshop.providers import Providers


def catalog_item(id, ctx=128000, iq=None, coding=None, reasoning=False, desc="", free=False, image=False):
    pricing = {"prompt": "0", "completion": "0"} if free else {"prompt": "0.000001", "completion": "0.000002"}
    bench = {"artificial_analysis": {"intelligence_index": iq, "coding_index": coding}} if iq is not None else {}
    return {"id": id, "context_length": ctx, "reasoning": reasoning, "description": desc, "pricing": pricing,
            "benchmarks": bench, "architecture": {"input_modalities": ["text", "image"] if image else ["text"]}}


def fake_catalog():
    items = [catalog_item("filler/m%d" % i, iq=float(i), coding=float(i * 2)) for i in range(40)]      # iq 0..39
    items += [
        catalog_item("google/gemini-2.5-pro", ctx=1_048_576, iq=38.0, coding=70.0, reasoning=True, image=True, desc="Flagship model."),
        catalog_item("meta-llama/llama-3.3-70b-instruct", iq=3.0, coding=5.0, desc="General chat."),
        catalog_item("qwen/qwen3-coder:free", ctx=262144, iq=20.0, coding=60.0, desc="Optimized for coding agents.", free=True),
    ]
    return {"data": items}


class ModelInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "modelinfo.json"
        self.calls = []

        def fetch():
            self.calls.append(1)
            return fake_catalog()
        self.info = ModelInfo(self.path, fetch=fetch)

    def test_names_from_different_services_match_one_catalog_entry(self):
        self.assertEqual(normalise("models/gemini-2.5-pro"), normalise("google/gemini-2.5-pro"))
        self.assertEqual(normalise("llama-3.3-70b-versatile"), normalise("meta-llama/llama-3.3-70b-instruct"))
        self.assertEqual(normalise("qwen/qwen3-coder:free"), normalise("qwen3-coder"))
        self.assertNotEqual(normalise("gemini-2.5-pro"), normalise("gemini-2.5-flash"))

    def test_refresh_downloads_once_a_day_and_keeps_a_compact_cache(self):
        self.assertTrue(self.info.refresh())
        self.assertFalse(self.info.refresh())                                 # fresh: no second download
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.info.refresh(now=self.info.fetched_at + 90000))   # stale after a day
        reloaded = ModelInfo(self.path, fetch=lambda: 1 / 0)                   # a new process reads the cache, no network
        self.assertEqual(len(reloaded.records), len(self.info.records))

    def test_describe_gives_tags_from_benchmarks_reasoning_context_and_images(self):
        self.info.refresh()
        top = self.info.describe("models/gemini-2.5-pro")
        self.assertIn("벤치마크 상위", top["tags"])
        self.assertIn("코드", top["tags"])
        self.assertIn("추론", top["tags"])
        self.assertIn("긴 문서(1M)", top["tags"])
        self.assertIn("이미지 입력", top["tags"])
        low = self.info.describe("llama-3.3-70b-versatile")
        self.assertIn("벤치마크 하위", low["tags"])
        self.assertTrue(self.info.describe("qwen3-coder")["desc"].startswith("Optimized"))
        self.assertIsNone(self.info.describe("never-heard-of-it"))

    def test_failed_or_junk_downloads_keep_the_old_data(self):
        self.info.refresh()
        count = len(self.info.records)
        self.info.fetch = lambda: (_ for _ in ()).throw(OSError("offline"))
        self.assertFalse(self.info.refresh(force=True))
        self.info.fetch = lambda: {"data": [{"id": "x/y"}]}                      # an error page / tiny list
        self.assertFalse(self.info.refresh(force=True))
        self.assertEqual(len(self.info.records), count)

    def test_installed_catalog_changes_the_router_view_of_a_model_but_name_guess_still_works_without_it(self):
        self.info.refresh()
        modelinfo.install(self.info)
        self.addCleanup(modelinfo.install, None)
        cap = modelpick.capability("llama-3.3-70b-versatile")
        self.assertTrue(cap["evidence"])
        self.assertEqual(cap["level"], 2)                                         # name says 3 (70b), benchmarks say 1: blended
        self.assertTrue(modelpick.capability("gemini-2.5-pro")["reasoning"])
        unknown = modelpick.capability("qwen2.5-coder:7b")
        self.assertFalse(unknown["evidence"])
        self.assertEqual((unknown["level"], unknown["coder"]), (1, True))
        modelinfo.install(None)
        self.assertFalse(modelpick.capability("llama-3.3-70b-versatile")["evidence"])


class ModelWatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.providers = Providers(self.dir / "providers.json")
        self.providers.add("gem", base_url="https://api.example.com/v1", data_policy="cloud-may-train", free_all=True, models=["gemini-2.5-flash"])
        self.providers.set_consent("gem")
        self.providers.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local", models=["qwen2.5-coder:7b"])
        self.listing = {"gem": ["models/gemini-2.5-flash", "models/gemini-2.5-pro"], "loc": ["qwen2.5-coder:7b"]}

        def list_fn(provider):
            name = "loc" if provider["data_policy"] == "local" else "gem"
            return self.listing[name]
        self.watch = ModelWatch(self.providers, list_fn)

    def test_first_look_only_remembers_then_new_models_are_added(self):
        first = self.watch.check(force=True)
        self.assertEqual((first["checked"], first["added"]), (2, {}))
        self.assertEqual(self.providers.get("gem")["models"], ["gemini-2.5-flash"])      # nothing added on first sight
        self.listing["gem"] += ["models/gemini-3-flash", "models/gemini-3-flash-preview-tts", "models/gemini-embedding-002"]
        self.listing["loc"] += ["llama3.2:3b"]
        second = self.watch.check(force=True)
        self.assertEqual(second["added"], {"gem": ["gemini-3-flash"], "loc": ["llama3.2:3b"]})   # chat models only
        self.assertIn("gemini-3-flash", self.providers.get("gem")["models"])
        self.assertIn("llama3.2:3b", self.providers.get("loc")["models"])
        third = self.watch.check(force=True)
        self.assertEqual(third["added"], {})                                           # already seen: nothing again

    def test_paid_or_unmarked_models_are_never_auto_added(self):
        self.providers.add("pay", base_url="https://pay.example.com/v1", data_policy="cloud-may-train", models=["a"])
        self.providers.set_consent("pay")
        self.listing["pay"] = ["a"]
        by_host = {"pay.example.com": "pay", "api.example.com": "gem", "127.0.0.1": "loc"}
        self.watch.list_fn = lambda p: self.listing[by_host[p["base_url"].split("/")[2].split(":")[0]]]
        self.watch.check(force=True)
        self.listing["pay"] = ["a", "expensive-model", "cheap-thing:free"]
        result = self.watch.check(force=True)
        self.assertEqual(result["added"].get("pay"), ["cheap-thing:free"])
        self.assertNotIn("expensive-model", self.providers.get("pay")["models"])

    def test_switch_off_records_but_adds_nothing(self):
        self.watch.check(force=True)
        self.watch.set_setting("auto_add_models", False)
        self.listing["gem"].append("models/gemini-3-flash")
        result = self.watch.check(force=True)
        self.assertEqual(result["added"], {})
        self.assertEqual(result["new"], {"gem": 1})
        self.assertNotIn("gemini-3-flash", self.providers.get("gem")["models"])
        with self.assertRaises(ValueError):
            self.watch.set_setting("nonsense", True)

    def test_news_is_kept_until_acknowledged_and_describes_new_models(self):
        info = ModelInfo(self.dir / "mi.json", fetch=fake_catalog)
        info.refresh()
        self.watch.info = info
        self.watch.check(force=True)
        self.listing["gem"].append("models/gemini-2.5-pro-2")
        self.listing["loc"].append("qwen3-coder")
        self.watch.check(force=True)
        news = self.watch.news()
        self.assertTrue(news)
        added = {m for n in news for m in n["added"]}
        self.assertIn("qwen3-coder", added)
        about = [a for n in news for a in n["about"] if a["model"] == "qwen3-coder"][0]
        self.assertIn("코드", about["tags"])
        self.watch.ack()
        self.assertEqual(self.watch.news(), [])

    def test_throttle_offline_services_and_unconsented_clouds(self):
        self.assertEqual(self.watch.check(now=1000)["checked"], 2)
        self.assertEqual(self.watch.check(now=1100)["skipped"], "recently checked")      # restarts do not hammer the services
        self.assertEqual(self.watch.check(now=1000 + 700)["skipped"], None)
        self.providers.add("nope", base_url="https://api.example.com/v1", data_policy="cloud-may-train", models=["x"])   # no consent
        called = []
        self.watch.list_fn = lambda p: called.append(p["base_url"]) or (_ for _ in ()).throw(OSError("offline"))
        result = self.watch.check(force=True)
        self.assertEqual(result["checked"], 0)                                           # offline: quietly nothing
        self.assertEqual(len(called), 2)                                                 # the unconsented service was never contacted

    def test_odd_names_from_a_remote_list_are_never_stored(self):
        self.watch.check(force=True)
        self.listing["gem"] += ["\ufeffmodels/gemini-3-flash", "x y", "bad;name", "models/ok-model-9"]
        result = self.watch.check(force=True)
        self.assertEqual(result["added"].get("gem"), ["ok-model-9"])
    def test_settings_default_on_and_survive(self):
        self.assertTrue(self.watch.settings()["auto_add_models"])
        self.watch.set_setting("auto_add_models", False)
        self.assertFalse(ModelWatch(self.providers, lambda p: []).settings()["auto_add_models"])


if __name__ == "__main__":
    unittest.main()
