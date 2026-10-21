import os
import stat
import tempfile
import unittest
from pathlib import Path

from workshop.chat_store import ChatStore, DEFAULT_TITLE, MAX_MESSAGES
from workshop.providers import Providers


class ChatStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ChatStore(Path(self.tmp.name) / "chats")

    def test_crud_title_and_ordering(self):
        a = self.store.create("m/x")
        b = self.store.create()
        self.assertEqual(self.store.get(a["id"])["title"], DEFAULT_TITLE)
        self.store.append(a["id"], "user", "  안녕   하세요, 오늘 날씨   어때?  ")
        self.store.append(a["id"], "assistant", "맑음")
        chat = self.store.get(a["id"])
        self.assertEqual(chat["title"], "안녕 하세요, 오늘 날씨 어때?")
        self.assertEqual([m["role"] for m in chat["messages"]], ["user", "assistant"])
        self.assertEqual(self.store.list()[0]["id"], a["id"])     # most recently updated first
        self.store.update(b["id"], title="이름 변경", model="p/y")
        self.assertEqual(self.store.get(b["id"])["model"], "p/y")
        self.assertEqual(self.store.list()[0]["id"], a["id"])     # renaming/model change does not reorder
        self.assertTrue(self.store.pop_last_assistant(a["id"]))
        self.assertFalse(self.store.pop_last_assistant(a["id"]))
        self.store.delete(a["id"])
        with self.assertRaises(LookupError):
            self.store.get(a["id"])

    def test_recency_order_holds_even_when_everything_happens_back_to_back(self):
        # CI machines are fast enough to touch two chats within the same clock tick.
        for _ in range(60):
            a, b = self.store.create(), self.store.create()
            self.store.append(a["id"], "user", "later than b was created")
            self.assertEqual(self.store.list()[0]["id"], a["id"])
            self.store.append(b["id"], "user", "even later")
            self.assertEqual(self.store.list()[0]["id"], b["id"])
            self.store.delete(a["id"]); self.store.delete(b["id"])
        stamps = [self.store.create()["updated"] for _ in range(200)]
        self.assertEqual(stamps, sorted(set(stamps)))              # strictly increasing, no duplicates

    def test_ids_are_validated_against_path_tricks(self):
        for bad in ("../x", "..", "", "a" * 36, "00000000-0000-0000-0000-00000000000/../x", None, 5,
                    "00000000-0000-0000-0000-000000000000.json"):
            with self.assertRaises(LookupError, msg=repr(bad)):
                self.store.get(bad)
            with self.assertRaises(LookupError):
                self.store.delete(bad)

    def test_limits_and_validation(self):
        chat = self.store.create()
        for bad in ("", "x" * 100_001, None):
            with self.assertRaises(ValueError):
                self.store.append(chat["id"], "user", bad)
        with self.assertRaises(ValueError):
            self.store.append(chat["id"], "system", "hi")
        with self.assertRaises(ValueError):
            self.store.update(chat["id"], title="")
        with self.assertRaises(ValueError):
            self.store.update(chat["id"], title="x" * 81)
        import workshop.chat_store as module
        old, module.MAX_MESSAGES = module.MAX_MESSAGES, 3
        try:
            for i in range(3):
                self.store.append(chat["id"], "user", "m" + str(i))
            with self.assertRaises(ValueError):
                self.store.append(chat["id"], "user", "one too many")
        finally:
            module.MAX_MESSAGES = old

    @unittest.skipUnless(os.name == "posix", "Unix file modes")
    def test_files_and_folder_are_private(self):
        chat = self.store.create()
        self.assertEqual(stat.S_IMODE(os.stat(self.store.folder / (chat["id"] + ".json")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.store.folder).st_mode), 0o700)

    def test_corrupt_files_are_skipped_in_listing(self):
        good = self.store.create()
        (self.store.folder / "11111111-1111-1111-1111-111111111111.json").write_text("{not json", encoding="utf-8")
        (self.store.folder / "notes.txt").write_text("x", encoding="utf-8")
        self.assertEqual([c["id"] for c in self.store.list()], [good["id"]])
        with self.assertRaises(LookupError):
            self.store.get("11111111-1111-1111-1111-111111111111")


class ProviderModelTests(unittest.TestCase):
    def test_reregistering_keeps_models_and_consent_and_models_are_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p = Providers(Path(d) / "providers.json")
            p.add("svc", base_url="https://api.example.com/v1", data_policy="cloud-may-train")
            p.add_model("svc", "a")
            p.add_model("svc", "b")
            p.add_model("svc", "a")                               # moves to the front, no duplicate
            p.set_consent("svc")
            self.assertEqual(p.load()["svc"]["models"], ["a", "b"])
            p.add("svc", base_url="https://api.example.com/v1", data_policy="cloud-may-train")
            self.assertEqual(p.load()["svc"]["models"], ["a", "b"])
            self.assertTrue(p.load()["svc"]["consented"])
            for i in range(80):
                p.add_model("svc", "m" + str(i))
            self.assertEqual(len(p.load()["svc"]["models"]), 50)
            with self.assertRaises(ValueError):
                p.add("svc", base_url="https://api.example.com/v1", models=["x" * 300])
            with self.assertRaises(ValueError):
                p.add_model("nope", "a")


if __name__ == "__main__":
    unittest.main()
