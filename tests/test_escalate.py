import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop import chat_api, modelescalate
from workshop.chat_api import Context
from workshop.providers import Providers
from tests.test_codework import Base


class FakeEngine:
    """Stands in for the built-in engine: no process, no download."""

    def __init__(self, installed=True, serving=False, ready_after_start=True):
        self.installed, self.is_serving, self.ready_after_start = installed, serving, ready_after_start
        self.starts = 0

    def status(self):
        return {"models": [{"id": modelescalate.BIG_ID, "installed": self.installed}], "running": self.is_serving, "ready": self.is_serving,
                "active": modelescalate.BIG_ID if self.is_serving else None}

    def start(self, model_id):
        self.starts += 1
        self.is_serving = self.ready_after_start


class HelperTests(unittest.TestCase):
    def test_ensure_serving_starts_the_engine_once_and_frees_the_gpu_first(self):
        engine, order = FakeEngine(), []
        engine_start = engine.start
        engine.start = lambda mid: (order.append("start"), engine_start(mid))
        ok = modelescalate.ensure_serving(engine, unload=lambda: order.append("unload"), sleep=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(order, ["unload", "start"])
        self.assertTrue(modelescalate.ensure_serving(engine, unload=lambda: order.append("again")))     # already serving: nothing more
        self.assertEqual(order, ["unload", "start"])

    def test_not_installed_means_nothing_is_started(self):
        engine = FakeEngine(installed=False)
        self.assertFalse(modelescalate.ensure_serving(engine, unload=lambda: self.fail("must not touch the GPU")))
        self.assertEqual(engine.starts, 0)

    def test_an_engine_that_never_becomes_ready_gives_false(self):
        clock = iter(range(0, 1000, 60))
        engine = FakeEngine(ready_after_start=False)
        self.assertFalse(modelescalate.ensure_serving(engine, unload=lambda: None, wait=100, sleep=lambda s: None, clock=lambda: next(clock)))

    def test_unload_ollama_asks_each_loaded_model_to_leave_and_survives_no_ollama(self):
        calls = []

        class Opener:
            def open(self, request, timeout=5):
                calls.append((request.get_method(), request.full_url, request.data))
                import io
                body = json.dumps({"models": [{"name": "qwen2.5-coder:7b"}]}).encode() if request.full_url.endswith("/api/ps") else b"{}"
                return io.BytesIO(body)
        self.assertEqual(modelescalate.unload_ollama(Opener()), 1)
        self.assertTrue(all(url.startswith("http://127.0.0.1:11434/") for _, url, _ in calls))             # loopback only
        self.assertEqual(json.loads(calls[1][2]), {"model": "qwen2.5-coder:7b", "keep_alive": 0})

        class Down:
            def open(self, request, timeout=5):
                raise OSError("no ollama")
        self.assertEqual(modelescalate.unload_ollama(Down()), 0)


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctx = Context(Providers(Path(self.tmp.name) / "providers.json"))
        self.ctx.unload_fn = lambda: None

    def test_prefer_big_follows_the_setting_and_the_installation(self):
        self.ctx.engine = FakeEngine(installed=False)
        self.assertIsNone(self.ctx.prefer_big())
        self.ctx.engine = FakeEngine(installed=True)
        self.assertEqual(self.ctx.prefer_big(), modelescalate.BIG_REF)
        self.ctx.watch.set_setting("code_prefer_big", False)
        self.assertIsNone(self.ctx.prefer_big())


class EscalationFlowTests(Base):
    """A failed direct-mode run is retried once with the 9B in a new copy; every other case does nothing."""

    def setUp(self):
        super().setUp()
        self.providers.add("jetai-engine", base_url="http://127.0.0.1:11500/v1", data_policy="local", models=[modelescalate.BIG_ID])
        self.ctx = Context(self.providers)
        self.ctx.unload_fn = lambda: None
        self.ctx.engine = FakeEngine(installed=True, serving=True)
        self.ctx.code.shutdown()
        self.ctx.code = self.runs                                                  # the test's CodeRuns (fake OpenCode, temp worktrees)
        self.runs.escalator = self.ctx.escalate_run
        self.started = []
        real_start = self.runs.start

        def spy(repo, prompt, model, *a, **k):
            self.started.append(model)
            return real_start(repo, prompt, model, *a, **k)
        self.runs.start = spy

    def finished_failed_run(self, mode="direct"):
        run_id = self.start("고쳐줘")
        self.wait(run_id)
        run = self.runs._read(run_id)
        run["status"], run["mode"], run["error"] = "failed", mode, "시험"
        self.runs._save(run)
        return run_id

    def test_a_failed_direct_run_is_retried_once_with_the_9b_and_the_two_are_linked(self):
        run_id = self.finished_failed_run()
        self.runs._maybe_escalate(self.runs._read(run_id))
        for _ in range(100):
            if self.runs._read(run_id).get("escalated_to"):
                break
            time.sleep(0.1)
        old = self.runs._read(run_id)
        self.assertTrue(old.get("escalated_to"))
        self.assertEqual(self.started[-1], modelescalate.BIG_REF)
        new = self.runs._read(old["escalated_to"])
        self.assertEqual(new["escalated_from"], run_id)
        self.assertEqual(new["model"], modelescalate.BIG_REF)
        self.wait(new["id"], timeout=60) if self.runs.get(new["id"])["status"] == "running" else None

    def test_nothing_happens_for_agent_mode_a_retry_itself_the_setting_off_or_no_9b(self):
        agent_run = self.finished_failed_run(mode="agent")
        self.runs._maybe_escalate(self.runs._read(agent_run))                       # OpenCode runs are not retried this way
        retry = self.finished_failed_run()
        record = self.runs._read(retry)
        record["escalated_from"] = "000000000000"
        self.runs._save(record)
        self.runs._maybe_escalate(self.runs._read(retry))                           # a retry never starts another retry
        self.ctx.watch.set_setting("code_auto_escalate", False)
        off = self.finished_failed_run()
        self.runs._maybe_escalate(self.runs._read(off))
        self.ctx.watch.set_setting("code_auto_escalate", True)
        self.ctx.engine = FakeEngine(installed=False)
        missing = self.finished_failed_run()
        self.runs._maybe_escalate(self.runs._read(missing))
        time.sleep(1.5)
        self.assertNotIn(modelescalate.BIG_REF, self.started)                       # no automatic run was started

    def test_a_successful_run_is_never_retried(self):
        run_id = self.start("고쳐줘")
        self.wait(run_id)
        run = self.runs._read(run_id)
        run["mode"], run["status"] = "direct", "review"
        self.runs._save(run)
        self.runs._maybe_escalate(self.runs._read(run_id))
        time.sleep(1.0)
        self.assertNotIn(modelescalate.BIG_REF, self.started)


if __name__ == "__main__":
    unittest.main()
