import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from workshop import pagestyle
from workshop.dashboard import make_handler
from workshop.providers import Providers
from workshop.queue import Queue
from workshop.usage import render_page, UsageLedger

OLD_HARDCODED = ("#111827", "#1f2937", "#93c5fd", "#374151")


class SharedLookTests(unittest.TestCase):
    def test_tokens_cover_light_dark_and_system_preference(self):
        css = pagestyle.CSS
        self.assertIn(":root[data-theme='dark']", css)
        self.assertIn(":root[data-theme='light']", css)
        self.assertIn("prefers-color-scheme:dark", css)
        for token in ("--bg", "--fg", "--border", "--accent", "--muted"):
            self.assertIn(token, css)

    def test_server_pages_use_the_shared_look_and_no_leftover_fixed_dark_colours(self):
        with tempfile.TemporaryDirectory() as d:
            server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
                Queue(Path(d) / "q.db"), "tok", boards_root=str(Path(d) / "boards"),
                providers=Providers(Path(d) / "p.json")))
            threading.Thread(target=server.serve_forever, daemon=True).start()
            self.addCleanup(lambda: (server.shutdown(), server.server_close()))
            base = "http://127.0.0.1:%d" % server.server_port
            for path in ("/", "/setup", "/boards"):
                html = urllib.request.urlopen(base + path).read().decode()
                self.assertIn("--accent", html, path)
                for color in OLD_HARDCODED:
                    self.assertNotIn(color, html, path + " still has " + color)

    def test_usage_page_uses_the_same_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            html = render_page(UsageLedger(Path(d) / "u.jsonl").summary())
        self.assertIn("--accent", html)
        for color in OLD_HARDCODED:
            self.assertNotIn(color, html)


if __name__ == "__main__":
    unittest.main()
