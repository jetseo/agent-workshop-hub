import json
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from workshop import chat, ctxprobe, diagnostics
from workshop.codework import CodeRuns
from tests.test_chat_api import ApiBase
from tests.test_codework import make_repo


class Server(BaseHTTPRequestHandler):
    """A local OpenAI-compatible fake that keeps only `cap` prompt tokens, like a server with a small context window."""
    cap = 2050

    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        chars = sum(len(m["content"]) for m in body["messages"])
        tokens = min(self.cap, chars // 3) if self.cap else None
        payload = {"choices": [{"message": {"content": "OK"}}]}
        if tokens is not None:
            payload["usage"] = {"prompt_tokens": tokens, "completion_tokens": 1}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(cap):
    handler = type("H", (Server,), {"cap": cap})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class PureTests(unittest.TestCase):
    def test_looks_cut_only_when_far_too_few_tokens_were_counted(self):
        self.assertFalse(ctxprobe.looks_cut(1000, 300))          # normal code
        self.assertFalse(ctxprobe.looks_cut(100, 40))            # tiny prompts say nothing
        self.assertTrue(ctxprobe.looks_cut(31000, 2050))         # the real Ollama case
        self.assertFalse(ctxprobe.looks_cut(31000, None))

    def test_probe_reports_the_limit_or_none(self):
        def capped(provider, model, messages, timeout, max_tokens, usage):
            usage["prompt_tokens"] = min(2050, sum(len(m["content"]) for m in messages) // 3)
        cut = ctxprobe.probe(capped, {}, "m")
        self.assertEqual((cut["truncated"], cut["limit_tokens"]), (True, 2050))
        self.assertIn("OLLAMA_CONTEXT_LENGTH", cut["advice"])
        self.assertIn("바꾸지 않습니다", cut["advice"])                                   # we only advise

        def full(provider, model, messages, timeout, max_tokens, usage):
            usage["prompt_tokens"] = sum(len(m["content"]) for m in messages) // 3
        ok = ctxprobe.probe(full, {}, "m")
        self.assertEqual((ok["truncated"], ok["limit_tokens"], ok["advice"]), (False, None, ""))
        silent = ctxprobe.probe(lambda *a, **k: None, {}, "m")
        self.assertFalse(silent["ok"])

    def test_results_are_kept_per_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            limits = ctxprobe.ContextLimits(Path(tmp) / "c.json")
            self.assertIsNone(limits.limit_tokens("a/b"))
            limits.set("a/b", {"limit_tokens": 2050})
            self.assertEqual(ctxprobe.ContextLimits(Path(tmp) / "c.json").limit_tokens("a/b"), 2050)


class ChatUsageTests(unittest.TestCase):
    def test_chat_fills_usage_and_stays_compatible(self):
        server = serve(2050)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        provider = {"base_url": "http://127.0.0.1:%d/v1" % server.server_port, "data_policy": "local"}
        usage = {}
        self.assertEqual(chat.chat(provider, "m", [{"role": "user", "content": "x" * 30000}], usage=usage), "OK")
        self.assertEqual(usage["prompt_tokens"], 2050)
        self.assertEqual(chat.chat(provider, "m", [{"role": "user", "content": "hi"}]), "OK")               # old call style unchanged
        result = ctxprobe.probe(chat.chat, provider, "m")
        self.assertTrue(result["truncated"])


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = make_repo(self.root)
        with open(self.repo / "calc.py", "a", encoding="utf-8", newline="\n") as handle:          # a long file, so the prompts are long
            handle.write("# " + "x" * 4000 + "\n")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, check=True)
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "long"], cwd=self.repo, check=True)
        from workshop.providers import Providers
        self.providers = Providers(self.root / "home" / "providers.json")
        self.providers.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local", models=["qwen2.5-coder:32b"])

    def run_with(self, chat_fn):
        replies = iter(['{"files": ["calc.py"]}', "<<<<<<< SEARCH\n    return a - b\n=======\n    return a + b\n>>>>>>> REPLACE"])
        runs = CodeRuns(self.providers, find_binary=lambda: None, worktree_base=self.root / "wt", direct_chat=chat_fn(replies))
        self.addCleanup(runs.shutdown)
        run = runs.start(str(self.repo), "고쳐줘", "loc/qwen2.5-coder:32b", mode="direct", validate=False)
        import time
        deadline = time.time() + 30
        while time.time() < deadline and runs.get(run["id"])["status"] == "running":
            time.sleep(0.1)
        return runs, runs.get(run["id"])

    def test_real_token_counts_are_recorded_and_a_cut_input_is_flagged(self):
        def factory(replies):
            def fake(provider, model, messages, timeout, max_tokens, temperature=None, usage=None):
                usage["prompt_tokens"], usage["completion_tokens"] = 1500, 20        # plausible for the characters sent
                return next(replies)
            fake.accepts_usage = True
            return fake
        runs, done = self.run_with(factory)
        self.assertEqual((done["stats"]["prompt_tokens"], done["stats"]["completion_tokens"], done["stats"]["cut_calls"]), (3000, 40, 0))
        self.assertIn("서비스가 센 토큰: 입력 3000, 출력 40", runs._path(done["id"]).joinpath("report.md").read_text(encoding="utf-8"))

        def cutting(replies):
            def fake(provider, model, messages, timeout, max_tokens, temperature=None, usage=None):
                usage["prompt_tokens"] = 600                                         # the long edit prompt cannot fit in 600 tokens
                return next(replies)
            fake.accepts_usage = True
            return fake
        runs, done = self.run_with(cutting)
        self.assertGreaterEqual(done["stats"]["cut_calls"], 1)
        report = runs._path(done["id"]).joinpath("report.md").read_text(encoding="utf-8")
        self.assertIn("입력이 잘렸을 수 있는 호출", report)

    def test_a_service_that_reports_nothing_is_not_guessed_about(self):
        runs, done = self.run_with(lambda replies: (lambda provider, model, messages, timeout, max_tokens, temperature=None: next(replies)))
        self.assertEqual(done["stats"]["prompt_tokens"], 0)
        self.assertIn("알 수 없음", runs._path(done["id"]).joinpath("report.md").read_text(encoding="utf-8"))

    def test_a_known_small_limit_shrinks_the_read_only_context(self):
        runs = CodeRuns(self.providers, find_binary=lambda: None, worktree_base=self.root / "wt")
        self.addCleanup(runs.shutdown)
        self.assertEqual(runs._context_chars({"model": "loc/x"}), 8000)
        runs.ctx_limits = ctxprobe.ContextLimits(self.root / "c.json")
        runs.ctx_limits.set("loc/x", {"limit_tokens": 2050})
        self.assertEqual(runs._context_chars({"model": "loc/x"}), 2460)
        self.assertEqual(runs._context_chars({"model": "loc/other"}), 8000)
        self.assertEqual([o["ctx_limit"] for o in runs.model_options() if o["ref"] == "loc/qwen2.5-coder:32b"], [None])
        runs.ctx_limits.set("loc/qwen2.5-coder:32b", {"limit_tokens": 2050})
        self.assertEqual([o["ctx_limit"] for o in runs.model_options() if o["ref"] == "loc/qwen2.5-coder:32b"], [2050])


class ApiAndCliTests(unittest.TestCase):
    def setUp(self):
        self.base = ApiBase("run")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.server = serve(2050)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base.ctx.providers.add("loc", base_url="http://127.0.0.1:%d/v1" % self.server.server_port, data_policy="local", models=["m"])
        self.base.ctx.providers.add("cloud", base_url="https://api.example.com/v1", data_policy="cloud-may-train", models=["x"])

    def call(self, body):
        return self.base.jcall("POST", "/code/context-probe", body)

    def test_probe_only_local_services_and_remember_the_result(self):
        status, result = self.call({"model": "loc/m"})
        self.assertEqual((status, result["truncated"], result["limit_tokens"]), (200, True, 2050))
        self.assertEqual(self.base.ctx.ctx_limits.limit_tokens("loc/m"), 2050)
        self.assertEqual([m["ctx_limit"] for m in self.base.jcall("GET", "/code")[1]["models"] if m["ref"] == "loc/m"], [2050])
        for bad in ({"model": "cloud/x"}, {"model": "nope/m"}, {"model": "loc"}, {}):
            self.assertEqual(self.call(bad)[0], 400, bad)                                         # cloud services never get test text

    def test_cli_refuses_unknown_and_cloud_services_without_calling_them(self):
        import contextlib
        import io
        from unittest import mock
        from workshop import providers as providers_module
        fake = self.base.ctx.providers
        with mock.patch.object(providers_module, "Providers", lambda *a, **k: fake), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(diagnostics._context_probe("nope/m"), 2)
            self.assertEqual(diagnostics._context_probe("cloud/x"), 2)
            self.assertEqual(diagnostics._context_probe("loc/m"), 0)
        self.assertIn("한도 약 2050토큰", out.getvalue())


if __name__ == "__main__":
    unittest.main()
