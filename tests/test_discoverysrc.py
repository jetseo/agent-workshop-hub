import io
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import chat_api, discoverysrc
from workshop.chat_api import ApiError, Context
from workshop.discovery import validate_card
from workshop.providers import Providers

NOW = 1_800_000_000.0
RECENT = "2027-01-10T00:00:00.000Z"          # about 5 days before NOW (NOW is 2027-01-15)
OLD = "2026-01-10T00:00:00.000Z"


class FakeOpener:
    """Answers the Hugging Face API from a dict; records every URL so a test can prove what was (not) requested."""

    def __init__(self, routes):
        self.routes, self.urls = routes, []

    def open(self, request, timeout=30):
        url = request.full_url if hasattr(request, "full_url") else request
        self.urls.append(url)
        for key, payload in self.routes.items():
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                body = io.BytesIO(json.dumps(payload).encode())
                body.__enter__ = lambda self=body: self
                body.__exit__ = lambda *a: False
                return body
        raise OSError("no route for " + url)


def detail(file_name="Model-Q4_K_M.gguf", size=5_000_000_000, tags=("license:apache-2.0",), gated=False):
    return {"siblings": [{"rfilename": file_name, "size": size, "lfs": {"size": size, "sha256": "ab" * 32}}], "tags": list(tags), "gated": gated}


HW = {"ram_gb": 64.0, "gpu": "RTX", "vram_gb": 12.0, "vram_free_gb": 10.0, "system": "Windows"}


def listing(*items):
    return [{"id": i[0], "downloads": i[1], "lastModified": i[2], "tags": []} for i in items]


class CandidateTests(unittest.TestCase):
    def run_candidates(self, routes, known=frozenset(), authors=("unsloth",)):
        opener = FakeOpener(routes)
        cards = discoverysrc.candidates(HW, opener=opener, now=NOW, known=set(known), authors=authors)
        return cards, opener

    def test_a_good_recent_model_becomes_a_valid_card_with_fit_and_no_performance_claim(self):
        cards, opener = self.run_candidates({"author=unsloth": listing(("unsloth/Foo-7B-Instruct-GGUF", 900, RECENT)), "api/models/unsloth/Foo-7B": detail()})
        self.assertEqual(len(cards), 1)
        card = validate_card(cards[0])                                             # the inbox would accept it as is
        self.assertEqual(card["kind"], "model")
        self.assertEqual(card["facts"]["라이선스"], "apache-2.0")
        self.assertIn("GiB", card["facts"]["파일 크기"])
        self.assertEqual(card["fit"], "gpu")
        self.assertFalse(card["measured"])
        self.assertTrue(card["source_url"].startswith("https://huggingface.co/"))
        self.assertTrue(all(u.startswith("https://huggingface.co/api/models") for u in opener.urls))     # only that host, only GET reads

    def test_unwanted_models_are_filtered_out(self):
        routes = {"author=unsloth": listing(
            ("unsloth/Known-9B-GGUF", 5, RECENT),              # already known to the engine
            ("unsloth/Tiny-1B-Instruct-GGUF", 5, RECENT),       # too small
            ("unsloth/Huge-70B-Instruct-GGUF", 5, RECENT),      # too big
            ("unsloth/Bge-7B-embed-GGUF", 5, RECENT),           # not a chat model
            ("unsloth/Stale-7B-Instruct-GGUF", 5, OLD),         # not recent
            ("unsloth/Beta-7B-preview-GGUF", 5, RECENT),        # unstable
            ("unsloth/Wan2.2-TI2V-5B-GGUF", 5, RECENT),       # a video model
            ("unsloth/Some-VL-7B-Instruct-GGUF", 5, RECENT),     # vision-language
            ("unsloth/Keep-8B-Instruct-GGUF", 5, RECENT)),
            "api/models/unsloth/Keep-8B": detail()}
        cards, _ = self.run_candidates(routes, known={"unsloth/Known-9B-GGUF"})
        self.assertEqual([c["id"] for c in cards], ["hf:unsloth/Keep-8B-Instruct-GGUF"])

    def test_gated_models_and_repos_without_a_single_q4_file_are_skipped(self):
        routes = {"author=unsloth": listing(("unsloth/A-7B-GGUF", 9, RECENT), ("unsloth/B-7B-GGUF", 8, RECENT), ("unsloth/C-7B-GGUF", 7, RECENT)),
                  "api/models/unsloth/A-7B": detail(gated=True),
                  "api/models/unsloth/B-7B": detail(file_name="B-7B-Q4_K_M-00001-of-00002.gguf"),     # split file: not one download
                  "api/models/unsloth/C-7B": detail(file_name="C-7B-Q8_0.gguf")}
        self.assertEqual(self.run_candidates(routes)[0], [])

    def test_a_model_too_big_for_this_pc_is_marked_not_recommended(self):
        routes = {"author=unsloth": listing(("unsloth/Big-30B-Instruct-GGUF", 1, RECENT)), "api/models/unsloth/Big-30B": detail(size=60_000_000_000)}
        cards, _ = self.run_candidates(routes)
        self.assertEqual(cards[0]["fit"], "no")

    def test_offline_gives_nothing_and_does_not_raise(self):
        cards, _ = self.run_candidates({"author=unsloth": OSError("offline")})
        self.assertEqual(cards, [])

    def test_the_card_count_is_capped_and_sorted_by_downloads(self):
        items = [("unsloth/M%d-7B-Instruct-GGUF" % i, i, RECENT) for i in range(1, 20)]
        routes = {"author=unsloth": listing(*items)}
        routes.update({"api/models/unsloth/M%d-7B" % i: detail() for i in range(1, 20)})
        cards, _ = self.run_candidates(routes)
        self.assertLessEqual(len(cards), discoverysrc.MAX_CARDS)
        self.assertEqual(cards[0]["id"], "hf:unsloth/M19-7B-Instruct-GGUF")


class RunApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = Context(Providers(Path(self.tmp.name) / "providers.json"))
        self.card = {"id": "hf:x/y", "kind": "model", "title": "Y", "source_url": "https://huggingface.co/x/y", "facts": {"파일 크기": "5 GiB"}}

    def wait(self):
        for _ in range(100):
            if not self.ctx.discovery_busy.is_set():
                return
            time.sleep(0.05)
        self.fail("the search did not finish")

    def test_nothing_is_requested_while_it_is_off(self):
        self.ctx.discovery_fn = lambda hw: self.fail("must not search while switched off")
        with self.assertRaises(ApiError):
            chat_api.dispatch(self.ctx, "POST", "/discovery/run", {})

    def test_a_run_adds_valid_cards_drops_bad_ones_and_records_the_result(self):
        chat_api.dispatch(self.ctx, "POST", "/discovery/enable", {"enabled": True})
        bad = {"id": "z", "kind": "install", "title": "x", "source_url": "https://evil.example/x"}
        self.ctx.discovery_fn = lambda hw: [self.card, bad]
        self.assertTrue(chat_api.dispatch(self.ctx, "POST", "/discovery/run", {}).payload["started"])
        self.wait()
        state = chat_api.dispatch(self.ctx, "GET", "/discovery", {}).payload
        self.assertEqual([c["id"] for c in state["cards"]], ["hf:x/y"])
        self.assertEqual(state["last_run"]["added"], 1)
        self.assertEqual(state["last_run"]["error"], "")

    def test_a_failure_keeps_the_old_cards_and_shows_the_error(self):
        chat_api.dispatch(self.ctx, "POST", "/discovery/enable", {"enabled": True})
        self.ctx.discovery.add_card(self.card)

        def boom(hw):
            raise OSError("offline")
        self.ctx.discovery_fn = boom
        chat_api.dispatch(self.ctx, "POST", "/discovery/run", {})
        self.wait()
        state = chat_api.dispatch(self.ctx, "GET", "/discovery", {}).payload
        self.assertEqual(len(state["cards"]), 1)
        self.assertIn("찾지 못했습니다", state["last_run"]["error"])

    def test_a_second_request_while_running_does_not_start_another(self):
        chat_api.dispatch(self.ctx, "POST", "/discovery/enable", {"enabled": True})
        gate, calls = threading.Event(), []

        def slow(hw):
            calls.append(1)
            gate.wait(5)
            return []
        self.ctx.discovery_fn = slow
        first = chat_api.dispatch(self.ctx, "POST", "/discovery/run", {}).payload
        second = chat_api.dispatch(self.ctx, "POST", "/discovery/run", {}).payload
        self.assertTrue(first["started"] and not second["started"])
        gate.set()
        self.wait()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
