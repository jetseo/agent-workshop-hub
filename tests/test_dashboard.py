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
            image = folder / "image-001-test.png"
            data = bytes.fromhex("89504e470d0a1a0a") + b"test-image"
            image.write_bytes(data)
            queue = Queue(folder / "jobs.db")
            job_id = queue.add("image", "Generate", {"workflow": "example.json", "output_dir": str(folder)}, require_approval=True)
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

    def test_http_rejects_bad_host_and_missing_csrf(self):
        from urllib.parse import urlencode
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / 'jobs.db')
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(queue, 'secret'))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = 'http://127.0.0.1:' + str(server.server_port)
                request = urllib.request.Request(base + '/', headers={'Host': 'evil.example'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 403)
                body = urlencode({'kind': 'plan', 'title': 'unauthorized'}).encode()
                request = urllib.request.Request(base + '/add', data=body, method='POST')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 403)
                self.assertEqual(queue.list(), [])
                origin_request = urllib.request.Request(base + '/add', data=urlencode({
                    'kind': 'plan', 'title': 'bad origin', 'token': 'secret'}).encode(),
                    headers={'Origin': 'https://evil.example'}, method='POST')
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(origin_request, timeout=5)
                self.assertEqual(error.exception.code, 403)
                self.assertEqual(queue.list(), [])
                request = urllib.request.Request(base + '/add', data=urlencode({
                    'kind': 'plan', 'title': 'authorized', 'token': 'secret'}).encode(), method='POST')
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                self.assertEqual(len(queue.list()), 1)
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
        item = {"path": str(Path.cwd().resolve() / "image-001-test.png"), "sha256": "a" * 64}
        job = {"kind": "image", "state": "review", "payload": json.dumps({"output_dir": str(Path.cwd().resolve())}), "result": json.dumps({"files": [item]})}
        self.assertEqual(len(gallery_files(job)), 1)
        item["path"] = str(Path.cwd().resolve().parent / "image-001-test.png")
        job["result"] = json.dumps({"files": [item]})
        self.assertEqual(gallery_files(job), [])
        item["path"] = str(Path.cwd().resolve() / "image-001-test.png")
        job["result"] = json.dumps({"files": [item]})
        job["state"] = "queued"
        self.assertEqual(gallery_files(job), [])
        job["state"] = "review"
        job["kind"] = "code"
        self.assertEqual(gallery_files(job), [])

    def test_gallery_rejects_missing_or_invalid_output_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / 'image-001-test.png'
            item = {'path': str(image), 'sha256': 'a' * 64}
            job = {'kind': 'image', 'state': 'review',
                   'result': json.dumps({'files': [item]})}
            self.assertEqual(gallery_files(job), [])
            job['payload'] = json.dumps({'output_dir': 'relative/path'})
            self.assertEqual(gallery_files(job), [])
            job['payload'] = json.dumps({'output_dir': directory})
            self.assertEqual(len(gallery_files(job)), 1)
            item['path'] = str(Path(directory) / 'other.png')
            job['result'] = json.dumps({'files': [item]})
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
