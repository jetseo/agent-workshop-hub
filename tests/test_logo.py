import re
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from workshop.dashboard import make_handler
from workshop.providers import Providers
from workshop.queue import Queue

STATIC = Path(__file__).resolve().parent.parent / "workshop" / "static"


class LogoTests(unittest.TestCase):
    def test_svg_files_are_static_art_without_scripts_or_external_references(self):
        for name in ("jetai-icon.svg", "jetai-mark.svg"):
            text = (STATIC / name).read_text(encoding="utf-8")
            self.assertTrue(text.lstrip().startswith("<svg"), name)
            self.assertNotRegex(text.lower(), r"<script|onload=|onclick=|href=|xlink|<image|javascript:", name)

    def test_ico_is_a_valid_multi_size_icon(self):
        data = (STATIC / "jetai.ico").read_bytes()
        self.assertEqual(data[:4], b"\x00\x00\x01\x00")
        count = int.from_bytes(data[4:6], "little")
        self.assertGreaterEqual(count, 5)
        for i in range(count):
            size, offset = int.from_bytes(data[6 + 16 * i + 8:6 + 16 * i + 12], "little"), int.from_bytes(data[6 + 16 * i + 12:6 + 16 * i + 16], "little")
            self.assertEqual(data[offset:offset + 8], b"\x89PNG\r\n\x1a\n")
            self.assertLessEqual(offset + size, len(data))

    def test_server_serves_the_logo_files_and_the_page_links_them(self):
        with tempfile.TemporaryDirectory() as d:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
                Queue(Path(d) / "q.db"), "tok", providers=Providers(Path(d) / "p.json")))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close()))
            base = "http://127.0.0.1:%d" % server.server_port
            for path, ctype in (("/static/jetai-icon.svg", "image/svg+xml"), ("/static/jetai-mark.svg", "image/svg+xml"),
                                ("/static/jetai.ico", "image/x-icon"), ("/favicon.ico", "image/x-icon")):
                response = urllib.request.urlopen(base + path)
                self.assertEqual(response.headers["Content-Type"], ctype, path)
                self.assertGreater(len(response.read()), 200, path)
            page = urllib.request.urlopen(base + "/app").read().decode()
            self.assertIn('href="/static/jetai-icon.svg"', page)
            self.assertRegex(page, r'class="brand"><svg class="logo"')


if __name__ == "__main__":
    unittest.main()
