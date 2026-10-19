import json
import tempfile
import unittest
from pathlib import Path
from workshop.board import Board
from workshop.orchestrator import create_board, run_board, main
from workshop.providers import Providers
from workshop import onboard


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.providers = Providers(self.dir / "providers.json")
        self.providers.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local")
        self.providers.add("free", base_url="https://api.example.com/v1", key_env="FREE_KEY",
                           data_policy="cloud-may-train")

    def board(self, steps):
        return create_board(self.dir / "boards", {"title": "T", "goal": "G", "steps": steps})


class OrchestratorTests(Fixture):
    def test_mixed_serial_parallel_flow(self):
        board = self.board([
            {"id": "a", "model": "loc/m1", "prompt": "A"},
            {"id": "b", "model": "loc/m2", "prompt": "B"},
            {"id": "c", "model": "loc/m3", "depends_on": ["a", "b"], "prompt": "C"}])
        calls = []
        def fake(provider, model, messages):
            calls.append(model); return "out-" + model
        summary = run_board(board, self.providers, text_fn=fake)
        self.assertEqual(sorted(summary["done"]), ["a", "b", "c"])
        self.assertEqual(calls[-1], "m3")
        self.assertIn("out-m1", messages_of(board, "c"))

    def test_failure_blocks_dependents_and_text_retries_once(self):
        board = self.board([{"id": "a", "model": "loc/m"}, {"id": "b", "model": "loc/m", "depends_on": ["a"]}])
        attempts = []
        def boom(provider, model, messages):
            attempts.append(1); raise RuntimeError("down")
        summary = run_board(board, self.providers, text_fn=boom)
        self.assertEqual(len(attempts), 2)
        self.assertIn("a", summary["failed"])
        self.assertEqual(summary["blocked"], ["b"])

    def test_cloud_and_training_gates(self):
        board = self.board([{"id": "a", "model": "free/m"}])
        s = run_board(board, self.providers, text_fn=lambda *a: "x")
        self.assertIn("--allow-cloud", s["failed"]["a"])
        board = self.board([{"id": "a", "model": "free/m"}])
        s = run_board(board, self.providers, allow_cloud=True, text_fn=lambda *a: "x")
        self.assertIn("--allow-may-train", s["failed"]["a"])
        board = self.board([{"id": "a", "model": "free/m"}])
        s = run_board(board, self.providers, allow_cloud=True, allow_may_train=True, text_fn=lambda *a: "x")
        self.assertEqual(s["done"], ["a"])

    def test_code_step_needs_flag_and_approval_and_is_never_retried(self):
        spec = [{"id": "c", "kind": "code", "model": "ollama/q", "workspace": str(self.dir)}]
        calls = []
        def code(prompt, ws, **kw):
            calls.append(kw); return {"exit_code": 1, "stdout": ""}
        s = run_board(self.board(spec), self.providers, code_fn=code)
        self.assertIn("approval", s["failed"]["c"])
        s = run_board(self.board(spec), self.providers, approved_code=["c"], code_fn=code)
        self.assertIn("--allow-opencode", s["failed"]["c"])
        s = run_board(self.board(spec), self.providers, approved_code=["c"], allow_opencode=True, code_fn=code)
        self.assertEqual(len(calls), 1)  # failed once, not retried
        self.assertIn("exited", s["failed"]["c"])

    def test_unregistered_provider_and_dry_run(self):
        s = run_board(self.board([{"id": "a", "model": "nope/m"}]), self.providers, text_fn=lambda *a: "x")
        self.assertIn("not registered", s["failed"]["a"])
        spec = self.dir / "spec.json"
        spec.write_text(json.dumps({"title": "T", "goal": "G", "steps": [{"id": "a", "model": "loc/m"}]}))
        self.assertEqual(main([str(spec), "--root", str(self.dir / "r"), "--dry-run"]), 0)


def messages_of(board, sid):
    return board.context_for(sid)


class ProviderTests(Fixture):
    def test_validation(self):
        bad = [("X", {"base_url": "https://a.com"}), ("a", {"base_url": "http://evil.com"}),
               ("a", {"base_url": "https://u:p@a.com"}), ("a", {"base_url": "https://a.com", "key_env": "sk-abc def"}),
               ("a", {"base_url": "https://a.com", "data_policy": "local"}),
               ("a", {"base_url": "https://a.com", "data_policy": "weird"})]
        for name, entry in bad:
            with self.assertRaises(ValueError, msg=str(entry)):
                self.providers.add(name, **entry)
        self.assertNotIn("sk-", (self.dir / "providers.json").read_text())

    def test_onboard_cli_and_consent(self):
        f = ["--file", str(self.dir / "p2.json")]
        self.assertEqual(onboard.main(f + ["add", "g", "--base-url", "https://x.com/v1", "--key-env", "G_KEY"]), 0)
        self.assertEqual(onboard.main(f + ["list"]), 0)
        self.assertEqual(onboard.main(f + ["test", "g", "m"]), 1)  # no --yes: no request sent
        self.assertEqual(onboard.main(f + ["remove", "g"]), 0)
        self.assertEqual(onboard.main(f + ["remove", "g"]), 1)


class BoardPageTests(unittest.TestCase):
    def test_dashboard_boards_view_escapes_and_validates_id(self):
        import threading, urllib.request, urllib.error
        from http.server import ThreadingHTTPServer
        from workshop.dashboard import make_handler
        from workshop.queue import Queue
        with tempfile.TemporaryDirectory() as d:
            board = Board.create(Path(d) / "boards", "<script>x</script>", "goal")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(Queue(Path(d) / "q.db"), "t", str(Path(d) / "boards")))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            base = "http://127.0.0.1:" + str(server.server_port)
            try:
                page = urllib.request.urlopen(base + "/board?id=" + board.folder.name).read().decode()
                self.assertIn("&lt;script&gt;", page)
                self.assertNotIn("<script>", page)
                self.assertIn(board.folder.name, urllib.request.urlopen(base + "/boards").read().decode())
                with self.assertRaises(urllib.error.HTTPError):
                    urllib.request.urlopen(base + "/board?id=../../etc")
            finally:
                server.shutdown(); server.server_close()


if __name__ == "__main__":
    unittest.main()
