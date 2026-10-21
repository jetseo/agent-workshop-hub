import tempfile
import json
import hashlib
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
import unittest
from pathlib import Path
from workshop.dashboard import make_handler
from workshop.queue import Queue

class DashboardTests(unittest.TestCase):
    def test_page_is_served_with_a_strict_content_security_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(queue, "test-token"))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen("http://127.0.0.1:" + str(server.server_port) + "/") as response:
                    policy = response.headers["Content-Security-Policy"]
                    self.assertIn("default-src 'none'", policy)
                    self.assertIn("form-action 'self'", policy)
                    self.assertNotIn("image", response.read().decode().lower().split("<select")[1].split("</select>")[0])
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

    def test_approval_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / "jobs.db")
            job_id = queue.add("code", "Write tests")
            with self.assertRaises(ValueError):
                queue.approve(job_id)
