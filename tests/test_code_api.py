import sys
import time
import unittest
from pathlib import Path

from tests.test_chat_api import ApiBase
from tests.test_codework import FAKE, git, make_repo
from workshop.codework import CodeRuns


class CodeApiTests(unittest.TestCase):
    def setUp(self):
        self.base = ApiBase("run")
        self.base.setUp()
        self.addCleanup(self.base.doCleanups)
        self.dir = Path(self.base.tmp.name)
        self.repo = make_repo(self.dir)
        script = self.dir / "fake_opencode.py"
        script.write_text(FAKE, encoding="utf-8")
        ctx = self.base.ctx
        ctx.providers.add("loc", base_url="http://127.0.0.1:11434/v1", data_policy="local", models=["qwen2.5-coder:32b"])
        ctx.code = CodeRuns(ctx.providers, find_binary=lambda: "fake", worktree_base=self.dir / "wt",
                            command_fn=lambda binary, model, prompt: [sys.executable, str(script), "run", "-m", model, prompt])
        self.addCleanup(ctx.code.shutdown)

    def call(self, method, path, body=None):
        return self.base.jcall(method, path, body)

    def wait(self, run_id, status):
        deadline = time.time() + 30
        while time.time() < deadline:
            run = self.call("GET", "/code/runs/" + run_id)[1]
            if run["status"] == status:
                return run
            time.sleep(0.1)
        self.fail("status %s not reached: %s" % (status, run["status"]))

    def test_overview_lists_environment_models_and_repos(self):
        status, body = self.call("GET", "/code")
        self.assertEqual(status, 200)
        self.assertTrue(body["environment"]["opencode"])
        self.assertIn("loc/qwen2.5-coder:32b", [m["ref"] for m in body["models"]])
        self.assertEqual(body["runs"], [])

    def test_full_flow_start_review_apply(self):
        status, run = self.call("POST", "/code/start", {"repo": str(self.repo), "prompt": "FIX the add function", "model": "loc/qwen2.5-coder:32b"})
        self.assertEqual(status, 200)
        done = self.wait(run["id"], "review")
        self.assertIn("+    return a + b", done["patch"])
        self.assertEqual(self.call("GET", "/code")[1]["repos"], [str(self.repo.resolve())])
        self.assertEqual(self.call("POST", "/code/runs/%s/apply" % run["id"], {})[0], 400)                       # no confirmation
        self.assertEqual(self.call("POST", "/code/runs/%s/apply" % run["id"], {"confirm": "yes"})[0], 400)        # must be the boolean true
        self.assertIn("a - b", (self.repo / "calc.py").read_text(encoding="utf-8"))
        status, applied = self.call("POST", "/code/runs/%s/apply" % run["id"], {"confirm": True})
        self.assertEqual((status, applied["status"]), (200, "applied"))
        self.assertIn("a + b", (self.repo / "calc.py").read_text(encoding="utf-8"))
        self.assertEqual(git(self.repo, "status", "--porcelain").strip(), "M calc.py")

    def test_discard_and_cancel(self):
        run = self.call("POST", "/code/start", {"repo": str(self.repo), "prompt": "SLEEP", "model": "loc/qwen2.5-coder:32b"})[1]
        self.assertEqual(self.call("POST", "/code/runs/%s/discard" % run["id"], {})[0], 400)                     # still running
        self.assertEqual(self.call("POST", "/code/runs/%s/cancel" % run["id"], {})[1]["id"], run["id"])
        self.wait(run["id"], "cancelled")
        self.assertEqual(self.call("POST", "/code/runs/%s/discard" % run["id"], {})[1]["status"], "discarded")

    def test_bad_requests_are_refused(self):
        for body in ({"repo": str(self.dir / "nope"), "prompt": "x"}, {"repo": str(self.repo), "prompt": ""}, {"repo": str(self.repo)},
                     {"repo": str(self.repo), "prompt": "x", "model": "opencode/claude-opus-5"}, {"repo": str(self.repo), "prompt": "x", "model": "ghost/m"}):
            self.assertEqual(self.call("POST", "/code/start", body)[0], 400, body)
        self.assertEqual(self.call("GET", "/code/runs/not-an-id")[0], 400)
        self.assertEqual(self.call("POST", "/code/runs/aaaaaaaaaaaa/apply", {"confirm": True})[0], 400)
        self.assertEqual(self.call("POST", "/code/nonsense", {})[0], 404)
        self.assertEqual(self.call("POST", "/code/runs/aaaaaaaaaaaa/explode", {})[0], 404)


if __name__ == "__main__":
    unittest.main()
