import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.discovery import Discovery, DiscoveryError, validate_card


def card(**kw):
    base = {"id": "hf:unsloth/x", "kind": "model", "title": "새 모델 X", "summary": "요약", "source_url": "https://huggingface.co/unsloth/x",
            "facts": {"크기": "5.3 GiB", "라이선스": "Apache-2.0"}, "fit": "gpu"}
    base.update(kw)
    return base


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "discovery.json"
        self.inbox = Discovery(self.path, clock=lambda: 100.0)

    def test_it_is_off_by_default_and_the_switch_is_remembered(self):
        self.assertFalse(self.inbox.state()["enabled"])
        self.inbox.set_enabled(True)
        self.assertTrue(Discovery(self.path).state()["enabled"])
        self.inbox.set_enabled("yes")                                                    # only a real true turns it on
        self.assertFalse(self.inbox.state()["enabled"])

    def test_cards_are_validated(self):
        for bad in (card(kind="install"), card(id=""), card(source_url="http://huggingface.co/x"), card(source_url="https://evil.example/x"),
                    card(source_url="https://user:pw@huggingface.co/x"), "not a dict"):
            with self.assertRaises(DiscoveryError):
                validate_card(bad)
        clean = validate_card(card(title="  길게   " + "가" * 500, facts={"k": "v" * 500}, fit="weird", extra="ignored"))
        self.assertLessEqual(len(clean["title"]), 120)
        self.assertLessEqual(len(clean["facts"]["k"]), 120)
        self.assertIsNone(clean["fit"])
        self.assertNotIn("extra", clean)
        self.assertFalse(clean["measured"])                                              # nothing is "measured" unless JetAI says so

    def test_duplicates_and_dismissed_cards_do_not_come_back(self):
        self.assertTrue(self.inbox.add_card(card()))
        self.assertFalse(self.inbox.add_card(card()))
        self.inbox.dismiss("hf:unsloth/x")
        self.assertEqual(self.inbox.state()["cards"], [])
        self.assertFalse(self.inbox.add_card(card()))
        again = Discovery(self.path)                                                     # after a restart
        self.assertFalse(again.add_card(card()))

    def test_unread_count_and_mark_read(self):
        self.inbox.add_card(card(id="a"))
        self.inbox.add_card(card(id="b"))
        self.assertEqual(self.inbox.state()["unread"], 2)
        self.inbox.mark_read()
        self.assertEqual(self.inbox.state()["unread"], 0)

    def test_the_inbox_is_capped(self):
        for i in range(250):
            self.inbox.add_card(card(id="c%d" % i))
        self.assertLessEqual(len(self.inbox.state()["cards"]), 200)

    def test_a_damaged_file_means_an_empty_inbox_not_a_crash(self):
        self.path.write_text("{not json", encoding="utf-8")
        self.assertEqual(self.inbox.state()["cards"], [])
        self.assertTrue(self.inbox.add_card(card()))


if __name__ == "__main__":
    unittest.main()
