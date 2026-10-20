import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

POSIX = os.name == "posix"


@unittest.skipUnless(POSIX, "the terminal backend runs in WSL/Linux")
class PtySessionTests(unittest.TestCase):
    def read_until(self, session, needle, seconds=5):
        deadline, seen = time.monotonic() + seconds, b""
        while time.monotonic() < deadline and needle not in seen:
            chunk = session.read(0.2)
            if chunk is None:
                break
            seen += chunk
        return seen

    def test_runs_commands_resizes_and_closes(self):
        from workshop.terminal import PtySession
        with tempfile.TemporaryDirectory() as d:
            session = PtySession(["sh"], d, cols=100, rows=30)
            self.addCleanup(session.close)
            session.write(b"echo $((6*7)); pwd; stty size\n")
            seen = self.read_until(session, b"30 100")
            self.assertIn(b"42", seen)
            self.assertIn(os.path.realpath(d).encode(), seen)
            self.assertIn(b"30 100", seen)
            session.resize(120, 40)
            session.write(b"stty size\n")
            self.assertIn(b"40 120", self.read_until(session, b"40 120"))
            session.close()
            self.assertIsNotNone(session.proc.poll())

    def test_exit_ends_the_output(self):
        from workshop.terminal import PtySession
        session = PtySession(["sh", "-c", "echo bye"], tempfile.gettempdir())
        self.addCleanup(session.close)
        seen, ended = b"", False
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            chunk = session.read(0.2)
            if chunk is None:
                ended = True
                break
            seen += chunk
        self.assertTrue(ended)
        self.assertIn(b"bye", seen)


if __name__ == "__main__":
    unittest.main()


import http.client
import struct
from http.server import ThreadingHTTPServer

from workshop.dashboard import make_handler
from workshop.providers import Providers
from workshop.queue import Queue

KEY = "start-up-key-for-tests"
CSRF = "csrf-token-for-tests"


def masked_text(text):
    payload = text.encode("utf-8")
    mask = b"\x01\x02\x03\x04"
    head = bytes([0x81, 0x80 | len(payload)]) if len(payload) < 126 else bytes([0x81, 0x80 | 126]) + struct.pack(">H", len(payload))
    return head + mask + bytes(b ^ mask[i & 3] for i, b in enumerate(payload))


class WsClient:
    def __init__(self, port, *, origin="auto", cookie=None, host="auto", path="/terminal/ws"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        lines = ["GET " + path + " HTTP/1.1",
                 "Host: " + (("127.0.0.1:%d" % port) if host == "auto" else host),
                 "Upgrade: websocket", "Connection: Upgrade",
                 "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13"]
        if origin:
            lines.append("Origin: " + (("http://127.0.0.1:%d" % port) if origin == "auto" else origin))
        if cookie:
            lines.append("Cookie: workshop_term=" + cookie)
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        head, _, self.buf = buf.partition(b"\r\n\r\n")
        self.status = int(head.split()[1])

    def _need(self, count):
        while len(self.buf) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError
            self.buf += chunk

    def frame(self):
        self._need(2)
        opcode, size = self.buf[0] & 0x0F, self.buf[1] & 0x7F
        offset = 2
        if size == 126:
            self._need(4); size = struct.unpack(">H", self.buf[2:4])[0]; offset = 4
        self._need(offset + size)
        payload, self.buf = self.buf[offset:offset + size], self.buf[offset + size:]
        return opcode, payload

    def send_json(self, **message):
        import json
        self.sock.sendall(masked_text(json.dumps(message)))

    def read_until(self, needle, seconds=6):
        deadline, seen = time.monotonic() + seconds, b""
        while time.monotonic() < deadline and needle not in seen:
            opcode, payload = self.frame()
            if opcode == 0x2:
                seen += payload
            elif opcode == 0x8:
                break
        return seen

    def close(self):
        self.sock.close()


@unittest.skipUnless(POSIX, "the terminal backend runs in WSL/Linux")
class TerminalHttpTests(unittest.TestCase):
    def start(self, *, terminal_key=KEY, argv=None):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(
            Queue(d / "q.db"), CSRF, str(d / "boards"), providers=Providers(d / "p.json"),
            terminal_key=terminal_key, terminal_argv=argv or ["sh"], terminal_cwd=str(d)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.port, self.dir = server.server_port, d
        return server

    def ws(self, port=None, **kwargs):
        client = WsClient(self.port, **kwargs)
        self.addCleanup(client.close)
        return client

    def get(self, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", path, headers=headers or {})
        response = conn.getresponse()
        body = response.read().decode("utf-8", "replace")
        return response, body

    def login(self):
        response, _ = self.get("/app?key=" + KEY)
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie")
        return cookie.split(";")[0].split("=", 1)[1], cookie

    def test_entry_key_cookie_and_page_states(self):
        self.start(terminal_key=None)
        response, body = self.get("/app")
        self.assertIn('data-terminal="off"', body)
        self.assertNotIn("터미널</button>", body)
        self.assertEqual(self.get("/terminal/ws")[0].status, 404)
        self.start()
        response, body = self.get("/app")
        self.assertIn('data-terminal="locked"', body)
        self.assertNotIn(CSRF, body)                       # the token is not handed to cookie-less callers
        self.assertEqual(self.get("/app?key=wrong")[0].status, 403)
        cookie_value, header = self.login()
        self.assertIn("HttpOnly", header)
        self.assertIn("SameSite=Strict", header)
        self.assertNotIn(KEY, header)                      # the cookie is not the URL key
        response, body = self.get("/app", {"Cookie": "workshop_term=" + cookie_value})
        self.assertIn('data-terminal="ready"', body)
        self.assertIn(CSRF, body)
        csp = response.getheader("Content-Security-Policy")
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("ws://127.0.0.1:%d" % self.port, csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertIn("frame-ancestors 'self'", self.get("/setup")[0].getheader("Content-Security-Policy"))

    def test_websocket_requires_cookie_origin_and_host(self):
        self.start()
        cookie, _ = self.login()
        self.assertEqual(self.ws(cookie=None).status, 403)
        self.assertEqual(self.ws(cookie="forged").status, 403)
        self.assertEqual(self.ws(cookie=cookie, origin="http://evil.example").status, 403)
        self.assertEqual(self.ws(cookie=cookie, origin=None).status, 403)
        self.assertEqual(self.ws(cookie=cookie, host="evil.example").status, 403)
        good = self.ws(cookie=cookie)
        self.assertEqual(good.status, 101)

    def test_wrong_token_never_spawns_a_process(self):
        marker = None
        self.start(argv=["sh", "-c", "touch marker; sleep 5"])
        marker = self.dir / "marker"
        cookie, _ = self.login()
        for hello in ({"t": "auth", "token": "wrong"}, {"t": "i", "d": "x"}):
            client = self.ws(cookie=cookie)
            client.send_json(**hello)
            opcode, _ = client.frame()
            self.assertEqual(opcode, 0x8)                  # server closes the connection
            client.close()
        time.sleep(0.5)
        self.assertFalse(marker.exists())

    def test_shell_works_and_dies_with_the_connection(self):
        self.start()
        cookie, _ = self.login()
        client = self.ws(cookie=cookie)
        client.send_json(t="auth", token=CSRF, cols=100, rows=30)
        client.send_json(t="i", d="echo $((6*7)) > out.txt; cat out.txt; echo $$ > pid.txt; stty size\n")
        seen = client.read_until(b"30 100")
        self.assertIn(b"42", seen)
        self.assertIn(b"30 100", seen)
        client.send_json(t="r", c=90, r=20)
        client.send_json(t="i", d="stty size\n")
        self.assertIn(b"20 90", client.read_until(b"20 90"))
        pid = int((self.dir / "pid.txt").read_text())
        client.close()
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.1)
        else:
            self.fail("shell process survived the connection")

    def test_session_limit(self):
        self.start()
        cookie, _ = self.login()
        clients = [self.ws(cookie=cookie) for _ in range(3)]
        for c in clients:
            self.assertEqual(c.status, 101)
        self.assertEqual(self.ws(cookie=cookie).status, 503)

    def test_entry_key_is_redacted_from_logs(self):
        import contextlib, io
        self.start()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.login()
            time.sleep(0.2)
        self.assertNotIn(KEY, out.getvalue())
        self.assertIn("key=***", out.getvalue())

    def test_static_files_are_whitelisted(self):
        self.start()
        response, body = self.get("/static/xterm.js")
        self.assertEqual(response.status, 200)
        self.assertIn("javascript", response.getheader("Content-Type"))
        self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
        for bad in ("/static/VENDOR.md", "/static/../dashboard.py", "/static/..%2fdashboard.py", "/static/LICENSE-xterm.txt"):
            self.assertEqual(self.get(bad)[0].status, 404, bad)
