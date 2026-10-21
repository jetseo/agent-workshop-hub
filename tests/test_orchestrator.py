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
        s = run_board(self.board(spec), self.providers, approved_code=["c"], allow_opencode=True,
                      allow_main_checkout=True, code_fn=code)
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
        from unittest.mock import patch
        with patch("workshop.onboard.getpass.getpass", return_value="sk-test-ABCDEFGH1234"):
            self.assertEqual(onboard.main(f + ["set-key", "g"]), 0)
            self.assertEqual(onboard.main(f + ["set-key", "nope"]), 1)
        self.assertEqual(Providers(self.dir / "p2.json").get("g")["api_key"], "sk-test-ABCDEFGH1234")
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


class ProgressTests(Fixture):
    def test_progress_reports_start_done_and_failure(self):
        board = self.board([{"id": "a", "model": "loc/m"}, {"id": "b", "model": "nope/m"}])
        lines = []
        run_board(board, self.providers, text_fn=lambda *a: "x", progress=lines.append)
        text = "\n".join(lines)
        self.assertIn("[start] a", text)
        self.assertIn("[done]  a", text)
        self.assertIn("[FAIL]  b", text)


class ResumeTests(Fixture):
    def test_resume_flow(self):
        ran = []
        def fail_b_once(provider, model, messages):
            text = messages[0]["content"]
            step = "b" if text.endswith("b") else "a" if text.endswith("a") else "c"
            ran.append(step)
            if step == "b" and ran.count("b") <= 2:   # both attempts of the first run fail
                raise RuntimeError("model unavailable")
            return "out"
        # prompts end with the step id so the fake can tell steps apart
        b2 = create_board(self.dir / "boards2", {"title": "T", "goal": "G", "steps": [
            {"id": "a", "model": "loc/m", "prompt": "a"}, {"id": "b", "model": "loc/m", "prompt": "b"},
            {"id": "c", "model": "loc/m", "depends_on": ["a", "b"], "prompt": "c"}]})
        first = run_board(b2, self.providers, text_fn=fail_b_once)
        self.assertEqual(first["done"], ["a"])
        self.assertEqual(first["blocked"], ["c"])
        self.assertEqual(b2.reset_failed(), ["b"])
        second = run_board(b2, self.providers, text_fn=fail_b_once)
        self.assertEqual(sorted(second["done"]), ["a", "b", "c"])
        self.assertEqual(ran.count("a"), 1)  # finished step was not re-run

    def test_failed_code_steps_need_explicit_retry(self):
        b = create_board(self.dir / "boards3", {"title": "T", "goal": "G", "steps": [
            {"id": "x", "kind": "code", "model": "ollama/q", "workspace": str(self.dir)}]})
        run_board(b, self.providers)  # fails: not approved
        self.assertEqual(b.reset_failed(), [])
        self.assertEqual(b.reset_failed(retry_code=["x"]), ["x"])

    def test_exit_error_includes_stderr(self):
        from workshop.orchestrator import _exit_error
        msg = _exit_error({"exit_code": 1, "stderr": "Error: model not available\n", "stdout": ""})
        self.assertIn("model not available", msg)


class ReferenceFileTests(Fixture):
    def make_root(self):
        root = self.dir / "proj"
        (root / "docs").mkdir(parents=True)
        (root / "README.md").write_text("install.ps1 exists", encoding="utf-8")
        (root / "docs" / "setup.md").write_text("setup notes", encoding="utf-8")
        (root / ".env").write_text("KEY=1", encoding="utf-8")
        (root / "auth.json").write_text("{}", encoding="utf-8")
        (self.dir / "outside.md").write_text("outside", encoding="utf-8")
        try:
            (root / "link.md").symlink_to(self.dir / "outside.md")
            self.has_symlink = True
        except (OSError, NotImplementedError):  # Windows without the symlink privilege
            self.has_symlink = False
        return root

    def spec(self, files):
        return {"title": "T", "goal": "G", "context_files": files,
                "steps": [{"id": "a", "model": "loc/m", "prompt": "Q"}]}

    def test_reference_files_reach_the_prompt_and_board(self):
        root = self.make_root()
        board = create_board(self.dir / "b", self.spec(["README.md", "docs/setup.md"]), context_root=root)
        prompt = board.context_for("a")
        self.assertIn("install.ps1 exists", prompt)
        self.assertIn("the ONLY source of facts", prompt)
        self.assertTrue(prompt.rstrip().endswith("Q"))
        self.assertIn("README.md", (board.folder / "board.md").read_text(encoding="utf-8"))

    def test_unsafe_references_are_refused(self):
        root = self.make_root()
        bad_specs = [[".env"], ["auth.json"], ["../outside.md"], [str(root / "README.md")],
                     ["missing.md"], [123], ["README.md"] * 11]
        if self.has_symlink:
            bad_specs.append(["link.md"])
        for bad in bad_specs:
            with self.assertRaises(ValueError, msg=str(bad)):
                create_board(self.dir / "bx", self.spec(bad), context_root=root)

    def test_oversized_reference_is_refused_and_trimmed_before_the_task(self):
        root = self.make_root()
        (root / "big.md").write_text("x" * 50_000, encoding="utf-8")
        with self.assertRaises(ValueError):
            create_board(self.dir / "bx", self.spec(["big.md"]), context_root=root)
        (root / "mid.md").write_text("y" * 30_000, encoding="utf-8")
        board = create_board(self.dir / "by", self.spec(["mid.md"]), context_root=root)
        prompt = board.context_for("a", max_chars=5000)
        self.assertLessEqual(len(prompt), 5100)
        self.assertIn("# Goal", prompt)
        self.assertTrue(prompt.rstrip().endswith("Q"))


class BoardUsageTests(Fixture):
    def setUp(self):
        super().setUp()
        from workshop.usage import UsageLedger
        self.ledger = UsageLedger(self.dir / "usage.jsonl")

    def records(self):
        return self.ledger._records()

    def run_steps(self, steps, **kw):
        board = self.board(steps)
        return run_board(board, self.providers, ledger=self.ledger, **kw)

    def test_text_steps_are_booked_by_tier_as_estimates(self):
        summary = self.run_steps([{"id": "a", "model": "loc/m1", "prompt": "안녕하세요 무엇을 도와드릴까요"},
                                  {"id": "b", "model": "free/big-free", "prompt": "q"},
                                  {"id": "c", "model": "free/pricey", "prompt": "q"}],
                                 allow_cloud=True, allow_may_train=True, text_fn=lambda p, m, msgs: "a reply of some length")
        self.assertEqual(sorted(summary["done"]), ["a", "b", "c"])
        by_model = {(r["provider"], r["model"]): r for r in self.records()}
        self.assertEqual(by_model[("loc", "m1")]["tier"], "local")
        self.assertEqual(by_model[("free", "big-free")]["tier"], "free")
        self.assertEqual(by_model[("free", "pricey")]["tier"], "other")      # a cloud model that is not marked free
        for record in by_model.values():
            self.assertTrue(record["estimated"])
            self.assertEqual(record["source"], "board")
            self.assertGreater(record["prompt"], 0)
            self.assertGreater(record["completion"], 0)
        self.assertEqual(self.ledger.summary()["by_source"]["board"]["requests"], 3)

    def test_opencode_text_and_code_steps_are_booked(self):
        fake = lambda prompt, ws, **kw: {"exit_code": 0, "stdout": "> build · m\n\nsome output text"}
        self.run_steps([{"id": "t", "model": "opencode/glm-5.3-flash-free", "prompt": "q"},
                        {"id": "c", "kind": "code", "model": "ollama/q", "workspace": str(self.dir), "prompt": "edit"}],
                       allow_opencode=True, approve_text_agents=True, approved_code=["c"], allow_cloud=True, allow_may_train=True,
                       allow_main_checkout=True, code_fn=fake)
        tiers = {r["model"]: r["tier"] for r in self.records()}
        self.assertEqual(tiers, {"glm-5.3-flash-free": "free", "q": "local"})

    def test_failed_steps_and_failed_attempts_are_not_booked(self):
        calls = []
        def flaky(provider, model, messages):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("temporary")
            return "ok now"
        self.run_steps([{"id": "a", "model": "loc/m", "prompt": "q"}], text_fn=flaky)
        self.assertEqual(len(self.records()), 1)                              # the retry that succeeded, once
        board_failed = self.run_steps([{"id": "x", "model": "loc/m", "prompt": "q"}],
                                      text_fn=lambda *a: (_ for _ in ()).throw(RuntimeError("down")))
        self.assertIn("x", board_failed["failed"])
        self.assertEqual(len(self.records()), 1)                              # nothing new for the failed step

    def test_no_ledger_means_nothing_is_written_and_ledger_errors_do_not_fail_steps(self):
        board = self.board([{"id": "a", "model": "loc/m", "prompt": "q"}])
        run_board(board, self.providers, text_fn=lambda *a: "ok")
        self.assertFalse(self.ledger.path.exists())
        class Broken:
            def record(self, **kw):
                raise OSError("disk full")
        summary = run_board(self.board([{"id": "a", "model": "loc/m", "prompt": "q"}]), self.providers,
                            text_fn=lambda *a: "ok", ledger=Broken())
        self.assertEqual(summary["done"], ["a"])

    def test_usage_page_shows_the_source_split(self):
        from workshop.usage import render_page
        self.run_steps([{"id": "a", "model": "loc/m", "prompt": "q"}], text_fn=lambda *a: "reply")
        self.ledger.record(provider="p", model="m", tier="free", prompt_tokens=10, completion_tokens=5, estimated=False)
        page = render_page(self.ledger.summary())
        self.assertIn("보드(멀티 모델 작업)", page)
        self.assertIn("채팅", page)
