import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import chat_api, engine as eng
from workshop.chat_api import ApiError, Context
from workshop.engine import Engine, EngineError
from workshop.providers import Providers
from tests.test_engine import FakeNet, Resp, HW_GPU

REPO = "unsloth/Foo-7B-Instruct-GGUF"
FILE = "Foo-7B-Instruct-Q4_K_M.gguf"
BODY = b"GGUF" + bytes(range(256)) * 20


class ExtNet(FakeNet):
    """FakeNet plus one more Hugging Face repo (the one a discovery card points at)."""
    gated = False

    def open(self, request, timeout=0):
        if request.full_url == eng.HF_API % REPO:
            sib = {"rfilename": FILE, "size": len(BODY), "lfs": {"sha256": hashlib.sha256(BODY).hexdigest(), "size": len(BODY)}}
            return Resp(json.dumps({"gated": self.ext_gated, "siblings": [sib], "tags": ["gguf", "license:apache-2.0"]}).encode())
        if request.full_url == eng.HF_FILE % (REPO, FILE):
            return Resp(BODY)
        return super().open(request, timeout)


class ExternalModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.net = ExtNet()
        self.net.ext_gated = False
        self.providers = Providers(self.dir / "providers.json")
        self.engine = Engine(self.dir / "engine", self.providers, opener=self.net, hardware_fn=lambda: HW_GPU)
        self.addCleanup(self.engine.stop)

    def test_a_registered_model_is_planned_with_the_exact_size_and_hash_and_appears_in_the_status(self):
        model_id = self.engine.register_external(REPO, FILE)
        plan = self.engine.plan(model_id)
        item = [i for i in plan["items"] if i["kind"] == "model"][0]
        self.assertEqual((item["name"], item["bytes"]), (FILE, len(BODY)))
        self.assertEqual(item["sha256"], hashlib.sha256(BODY).hexdigest())
        self.assertEqual(plan["license"], "apache-2.0")
        listed = {m["id"]: m for m in self.engine.status()["models"]}
        self.assertIn(model_id, listed)
        self.assertFalse(listed[model_id]["installed"])
        self.assertTrue(all(u.startswith("https://") for u, _ in self.net.requests))        # nothing was downloaded to plan

    def test_it_survives_a_restart_and_installing_needs_the_exact_bytes(self):
        model_id = self.engine.register_external(REPO, FILE)
        again = Engine(self.dir / "engine", self.providers, opener=self.net, hardware_fn=lambda: HW_GPU)
        plan = again.plan(model_id)
        with self.assertRaises(EngineError):
            again.install(model_id, plan["total_bytes"] + 1)                                # a wrong confirmation never starts a download
        self.assertFalse(again.job["running"])

    def test_bad_names_gated_and_unknown_repos_are_refused(self):
        for repo, file in (("../etc/passwd", FILE), (REPO, "Foo.safetensors"), (REPO, "../" + FILE), ("nofolder", FILE), (REPO, "Foo-Q8_0.gguf")):
            with self.assertRaises(EngineError):
                self.engine.register_external(repo, file)
        self.net.ext_gated = True
        with self.assertRaises(EngineError):
            self.engine.register_external(REPO, FILE)
        with self.assertRaises(EngineError):
            self.engine.plan("ext-never-registered")

    def test_built_in_models_still_work(self):
        self.assertTrue(self.engine.plan("qwen2.5-0.5b")["items"])


class RegisterApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.ctx = Context(Providers(self.dir / "providers.json"))
        self.net = ExtNet()
        self.net.ext_gated = False
        self.ctx.engine = Engine(self.dir / "engine", self.ctx.providers, opener=self.net, hardware_fn=lambda: HW_GPU)
        self.addCleanup(self.ctx.engine.stop)

    def test_only_models_that_are_on_a_discovery_card_can_be_registered(self):
        with self.assertRaises(ApiError):
            chat_api.dispatch(self.ctx, "POST", "/engine/register", {"repo": REPO, "file": FILE})        # no such card
        self.ctx.discovery.add_card({"id": "hf:" + REPO, "kind": "model", "title": "Foo", "source_url": "https://huggingface.co/" + REPO,
                                     "facts": {"파일": FILE}})
        result = chat_api.dispatch(self.ctx, "POST", "/engine/register", {"repo": REPO, "file": FILE}).payload
        self.assertEqual(result["total_bytes"], len(BODY) + [i for i in result["items"] if i["kind"] == "binary"][0]["bytes"])
        with self.assertRaises(ApiError):
            chat_api.dispatch(self.ctx, "POST", "/engine/register", {"repo": REPO, "file": "Other-Q4_K_M.gguf"})   # the card names another file


if __name__ == "__main__":
    unittest.main()
