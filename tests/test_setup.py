import json
import os
import stat
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from workshop import setup_page
from workshop.dashboard import make_handler
from workshop.providers import Providers, mask_key
from workshop.queue import Queue

KEY = "sk-test-SECRET-1234567890abcd"


def assert_private(test, path):
    """0600 is enforced on Linux/WSL (ext4). NTFS has no Unix mode bits (always reports 0o666)."""
    if os.name != "nt":
        test.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)


def form(**values):
    return {k: [v] for k, v in values.items()}


class TokenStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.providers = Providers(self.dir / "providers.json")

    def test_key_is_stored_privately_and_never_in_provider_metadata(self):
        self.providers.add("svc", base_url="https://api.example.com/v1", data_policy="cloud-may-train")
        self.providers.set_key("svc", KEY)
        assert_private(self, self.providers.secrets_path)
        self.assertNotIn(KEY, (self.dir / "providers.json").read_text(encoding="utf-8"))
        self.assertNotIn("api_key", self.providers.load()["svc"])
        self.assertEqual(self.providers.get("svc")["api_key"], KEY)
        self.assertEqual(self.providers.key_status("svc"), "saved " + mask_key(KEY))
        self.assertNotIn(KEY, self.providers.key_status("svc"))

    def test_remove_clears_the_token_too(self):
        self.providers.add("svc", base_url="https://api.example.com/v1")
        self.providers.set_key("svc", KEY)
        self.providers.remove("svc")
        self.assertNotIn(KEY, self.providers.secrets_path.read_text(encoding="utf-8"))
        self.assertIsNone(self.providers.get("svc"))

    def test_bad_tokens_are_rejected(self):
        for bad in ("short", "has space in it 123", "line\nbreak12345", "x" * 600, ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                self.providers.set_key("svc", bad)


class RunTestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.providers = Providers(Path(self.tmp.name) / "providers.json")
        self.calls = []

    def fake_chat(self, provider, model, messages, **kw):
        self.calls.append((provider, model, messages, kw))
        return "  hello from model \n"

    def submit(self, chat_fn=None, **values):
        base = dict(service="groq", key=KEY, model="m1", prompt="hi", consent="on", save="on")
        base.update(values)
        return setup_page.run_test(self.providers, form(**base), chat_fn or self.fake_chat)

    def test_success_saves_provider_and_token(self):
        result = self.submit()
        self.assertTrue(result["ok"] and result["saved"])
        self.assertEqual(result["reply"], "hello from model")
        provider, model, messages, kw = self.calls[0]
        self.assertEqual((provider["api_key"], model, messages[0]["content"]), (KEY, "m1", "hi"))
        self.assertEqual(kw["max_tokens"], 300)
        self.assertEqual(self.providers.get("groq")["api_key"], KEY)
        self.assertEqual(self.providers.load()["groq"]["data_policy"], "cloud-may-train")

    def test_nothing_is_saved_on_failure_or_when_unchecked(self):
        def boom(*a, **k):
            raise RuntimeError("Provider returned HTTP 401 (token rejected)")
        self.assertFalse(self.submit(boom)["ok"])
        self.assertEqual(self.providers.load(), {})
        self.assertFalse(self.providers.secrets_path.exists())
        result = self.submit(save="")
        self.assertTrue(result["ok"] and not result["saved"])
        self.assertEqual(self.providers.load(), {})

    def test_cloud_requires_consent_but_local_does_not(self):
        self.assertIn("consent", self.submit(consent="")["error"].lower())
        self.assertEqual(self.calls, [])
        result = self.submit(service="ollama", key="", consent="", model="llama3.2:3b")
        self.assertTrue(result["ok"])
        self.assertIsNone(self.calls[-1][0]["api_key"])

    def test_error_text_never_contains_the_token(self):
        def leaky(*a, **k):
            raise RuntimeError("upstream said: bad key " + KEY)
        error = self.submit(leaky)["error"]
        self.assertNotIn(KEY, error)
        self.assertIn("***", error)

    def test_missing_token_model_prompt_and_stored_key_reuse(self):
        self.assertIn("token", self.submit(key="")["error"].lower())
        self.assertIn("model", self.submit(model="")["error"].lower())
        self.assertIn("Prompt", self.submit(prompt="")["error"])
        self.assertIn("Prompt", self.submit(prompt="x" * 2001)["error"])
        self.assertEqual(self.calls, [])
        self.assertTrue(self.submit()["saved"])
        self.assertTrue(self.submit(key="")["ok"])           # reuses the stored token
        self.assertEqual(self.calls[-1][0]["api_key"], KEY)

    def test_custom_service_is_validated(self):
        for values in (dict(custom_name="Bad Name", custom_url="https://a.com/v1"),
                       dict(custom_name="ok", custom_url="http://evil.example.com/v1"),
                       dict(custom_name="ok", custom_url="https://u:p@a.com/v1")):
            self.assertFalse(self.submit(service="custom", **values)["ok"], values)
        self.assertTrue(self.submit(service="custom", custom_name="mine", custom_url="https://a.com/v1")["ok"])


class HttpSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.providers = Providers(d / "providers.json")
        self.replies = ["hello <script>alert(1)</script>"]
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            Queue(d / "q.db"), "csrf-token", str(d / "boards"), providers=self.providers,
            chat_fn=lambda p, m, msgs, **kw: self.replies[0], list_fn=lambda p: ["a:free", "b", "c-free"]))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))
        self.base = "http://127.0.0.1:" + str(self.server.server_port)

    def post(self, path, headers=None, **values):
        data = urllib.parse.urlencode(values).encode()
        request = urllib.request.Request(self.base + path, data=data, headers=headers or {}, method="POST")
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def run_form(self, **extra):
        values = dict(token="csrf-token", service="groq", key=KEY, model="m1", prompt="안녕" * 900,
                      consent="on", save="on")
        values.update(extra)
        return self.post("/setup/run", **values)

    def test_full_flow_never_echoes_the_token(self):
        page = urllib.request.urlopen(self.base + "/setup").read().decode()
        self.assertIn('action="/setup/run"', page)
        status, body = self.run_form()          # 1800 Korean chars: larger than the default body limit
        self.assertEqual(status, 200)
        self.assertIn("성공", body)
        self.assertIn("&lt;script&gt;", body)   # model output is escaped
        self.assertNotIn("<script>alert", body)
        self.assertNotIn(KEY, body)
        listing = urllib.request.urlopen(self.base + "/setup").read().decode()
        self.assertNotIn(KEY, listing)
        self.assertIn(mask_key(KEY), listing)
        self.assertNotIn(KEY, (Path(self.tmp.name) / "providers.json").read_text(encoding="utf-8"))
        assert_private(self, self.providers.secrets_path)

    def test_csrf_origin_and_host_are_enforced(self):
        self.assertEqual(self.run_form(token="wrong")[0], 403)
        self.assertEqual(self.post("/setup/run", headers={"Origin": "http://evil.example"},
                                   token="csrf-token", service="groq")[0], 403)
        request = urllib.request.Request(self.base + "/setup", headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(request)
        self.assertEqual(ctx.exception.code, 403)
        self.assertFalse(self.providers.secrets_path.exists())

    def test_models_listing_and_removal(self):
        self.run_form()
        status, body = self.post("/setup/models", token="csrf-token", name="groq")
        self.assertIn("a:free", body)
        self.assertIn("c-free", body)
        status, _ = self.post("/setup/remove", token="csrf-token", name="groq")
        self.assertIn(status, (200, 303))
        self.assertEqual(self.providers.load(), {})
        self.assertNotIn(KEY, self.providers.secrets_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class RedirectSafetyTests(unittest.TestCase):
    def test_token_is_never_forwarded_through_a_redirect(self):
        from http.server import BaseHTTPRequestHandler
        from workshop.chat import chat, list_models
        seen = []

        class Target(BaseHTTPRequestHandler):
            def do_ANY(self):
                seen.append(self.headers.get("Authorization"))
                self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
            do_GET = do_POST = do_ANY
            def log_message(self, *a): pass

        target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
        target_url = "http://127.0.0.1:" + str(target.server_port) + "/stolen"

        class Redirector(BaseHTTPRequestHandler):
            def do_ANY(self):
                self.send_response(307); self.send_header("Location", target_url)
                self.send_header("Content-Length", "0"); self.end_headers()
            do_GET = do_POST = do_ANY
            def log_message(self, *a): pass

        redirector = ThreadingHTTPServer(("127.0.0.1", 0), Redirector)
        for server in (target, redirector):
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(lambda s=server: (s.shutdown(), s.server_close()))
        provider = {"base_url": "http://127.0.0.1:" + str(redirector.server_port) + "/v1", "api_key": KEY}
        with self.assertRaises(RuntimeError) as ctx:
            chat(provider, "m", [{"role": "user", "content": "hi"}], timeout=5)
        self.assertIn("HTTP 307", str(ctx.exception))
        self.assertNotIn(KEY, str(ctx.exception))
        with self.assertRaises(RuntimeError):
            list_models(provider, timeout=5)
        self.assertEqual(seen, [])  # the second server never saw a request, let alone the token
