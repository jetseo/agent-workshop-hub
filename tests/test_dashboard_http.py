"""HTTP integration tests for the localhost dashboard."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from workshop.dashboard import make_handler
from workshop.queue import Queue


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardHTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.tmp.name) / "jobs.sqlite3")
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(self.queue, "integration-token")
        )
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        self.client = urllib.request.build_opener(NoRedirect())

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.tmp.cleanup()

    def post(self, path, **fields):
        data = urllib.parse.urlencode(fields).encode("utf-8")
        request = urllib.request.Request(self.url + path, data=data, method="POST")
        try:
            return self.client.open(request, timeout=3).status
        except urllib.error.HTTPError as error:
            return error.code

    def test_model_field_is_stored_for_code_tasks(self):
        self.assertEqual(self.post("/add", token="integration-token", kind="code", title="Fix",
                                   resource="/tmp/worktree", model="opencode/big-pickle-free"), 303)
        import json
        self.assertEqual(json.loads(self.queue.list()[0]["payload"]),
                         {"workspace": "/tmp/worktree", "model": "opencode/big-pickle-free"})

    def test_create_authorize_and_approve(self):
        self.assertEqual(
            self.post("/add", token="integration-token", kind="code",
                      title="Write tests", resource="/tmp/worktree"), 303
        )
        task = self.queue.list()[0]
        self.assertEqual(task["state"], "pending_approval")
        self.assertIsNone(self.queue.claim(kinds=["code"]))
        self.assertEqual(self.post("/authorize", token="integration-token", id=task["id"]), 303)
        self.assertEqual(self.queue.claim(kinds=["code"])["id"], task["id"])
        self.queue.finish(task["id"], {"ok": True})
        self.assertEqual(self.post("/approve", token="integration-token", id=task["id"]), 303)
        self.assertEqual(self.queue.list()[0]["state"], "done")

    def test_reject_missing_token(self):
        self.assertEqual(self.post("/add", kind="code", title="Unsafe", resource="/tmp"), 403)
        self.assertEqual(self.queue.list(), [])

    def test_reject_missing_resource(self):
        self.assertEqual(
            self.post("/add", token="integration-token", kind="code", title="Sprite"), 400
        )
        self.assertEqual(self.queue.list(), [])

    def test_escapes_untrusted_title(self):
        self.queue.add("plan", "<script>alert(1)</script>")
        with urllib.request.urlopen(self.url + "/", timeout=3) as response:
            page = response.read().decode("utf-8")
        self.assertNotIn("<script>", page)
        self.assertIn("&lt;script&gt;", page)

    def test_reject_unexpected_host(self):
        request = urllib.request.Request(
            self.url + "/", headers={"Host": "attacker.example"}
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request, timeout=3)
        self.assertEqual(error.exception.code, 403)

    def test_reject_cross_origin_write(self):
        data = urllib.parse.urlencode({
            "token": "integration-token", "kind": "plan", "title": "Injected"
        }).encode("utf-8")
        request = urllib.request.Request(
            self.url + "/add", data=data, method="POST",
            headers={"Origin": "https://attacker.example"}
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.client.open(request, timeout=3)
        self.assertEqual(error.exception.code, 403)
        self.assertEqual(self.queue.list(), [])

    def test_api_redacts_payload_and_result(self):
        self.queue.add("code", "Task", {"workspace": "/private/workspace"})
        with urllib.request.urlopen(self.url + "/api/jobs", timeout=3) as response:
            raw = response.read().decode("utf-8")
        self.assertNotIn("/private/workspace", raw)
        self.assertNotIn("payload", raw)
        self.assertNotIn("result", raw)

    def test_api_jobs(self):
        self.queue.add("plan", "Outline")
        with urllib.request.urlopen(self.url + "/api/jobs", timeout=3) as response:
            jobs = json.load(response)
        self.assertEqual(jobs[0]["title"], "Outline")


if __name__ == "__main__":
    unittest.main()
