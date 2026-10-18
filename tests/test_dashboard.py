import tempfile
import json
import hashlib
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
import unittest
from pathlib import Path
from workshop.dashboard import make_handler, gallery_files, gallery_signature
from workshop.queue import Queue

class DashboardTests(unittest.TestCase):
    def test_gallery_http_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            image = folder / "render.png"
            data = bytes.fromhex("89504e470d0a1a0a") + b"test-image"
            image.write_bytes(data)
            queue = Queue(folder / "jobs.db")
            job_id = queue.add("image", "Generate", {"workflow": "example.json"}, require_approval=True)
            queue.authorize(job_id)
            queue.claim(kinds=["image"])
            queue.finish(job_id, {"files": [{"path": str(image),
                                            "sha256": hashlib.sha256(data).hexdigest()}]})
            token = "test-token"
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(queue, token))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:" + str(server.server_port)
                with urllib.request.urlopen(base + "/") as response:
                    page = response.read().decode()
                self.assertIn("Generated image", page)
                url = (base + "/image?id=" + job_id + "&index=0&sig=" +
                       gallery_signature(token, job_id, 0))
                with urllib.request.urlopen(url) as response:
                    self.assertEqual(response.read(), data)
                    self.assertEqual(response.headers["Content-Type"], "image/png")
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(base + "/image?id=" + job_id + "&index=0&sig=invalid")
                self.assertEqual(failure.exception.code, 403)
                image.write_bytes(b"tampered")
                with self.assertRaises(urllib.error.HTTPError) as failure:
                    urllib.request.urlopen(url)
                self.assertEqual(failure.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)

    def test_handler_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            handler = make_handler(queue, "test-token")
            self.assertTrue(callable(handler.do_GET))
            self.assertTrue(callable(handler.do_POST))

    def test_gallery_only_for_reviewed_image_results(self):
        import json
        item = {"path": str(Path.cwd().resolve() / "render.png"), "sha256": "a" * 64}
        job = {"kind": "image", "state": "review", "result": json.dumps({"files": [item]})}
        self.assertEqual(len(gallery_files(job)), 1)
        job["state"] = "queued"
        self.assertEqual(gallery_files(job), [])
        job["state"] = "review"
        job["kind"] = "code"
        self.assertEqual(gallery_files(job), [])

    def test_gallery_signature_scoped_to_job_and_index(self):
        self.assertNotEqual(gallery_signature("token", "a", 0),
                            gallery_signature("token", "a", 1))
        self.assertNotEqual(gallery_signature("token", "a", 0),
                            gallery_signature("token", "b", 0))

    def test_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            job_id = queue.add("code", "Write tests")
            with self.assertRaises(ValueError):
                queue.approve(job_id)
