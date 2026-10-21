"""Browser terminal backend: a login shell on a pseudo-terminal, bridged to a WebSocket.

POSIX only (it runs inside WSL/Linux). This is a shell as the current user, so the dashboard exposes
it only with --enable-terminal, behind a start-up key, an Origin check and a per-run token.

Client -> server messages (text JSON): {"t":"auth","token":..,"cols":..,"rows":..} first, then
{"t":"i","d":"typed text"} and {"t":"r","c":cols,"r":rows}. Server -> client: binary terminal output.
"""
import json
import os
import select
import signal
import subprocess
import threading
import time

from .websocket import TEXT, WebSocket, WebSocketClosed, accept_key

MAX_SESSIONS = 3
MAX_INPUT = 65536
AUTH_TIMEOUT = 10.0
_sessions = threading.BoundedSemaphore(MAX_SESSIONS)


def default_argv():
    return [os.environ.get("WORKSHOP_TERMINAL_SHELL", "bash"), "-l", "-i"]


class PtySession:
    def __init__(self, argv, cwd, cols=80, rows=24, env=None):
        import fcntl
        import struct
        import termios
        self._fcntl, self._struct, self._termios = fcntl, struct, termios
        self.master, slave = os.openpty()
        self._set_size(self.master, cols, rows)
        child_env = dict(os.environ if env is None else env)
        child_env.update(TERM="xterm-256color", COLORTERM="truecolor", WORKSHOP_TERMINAL="1")
        try:
            self.proc = subprocess.Popen(
                argv, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=child_env, close_fds=True,
                start_new_session=True,                     # new session; the pty becomes its controlling tty
                preexec_fn=lambda: fcntl.ioctl(0, termios.TIOCSCTTY, 0))
        finally:
            os.close(slave)

    def _set_size(self, fd, cols, rows):
        self._fcntl.ioctl(fd, self._termios.TIOCSWINSZ, self._struct.pack("HHHH", rows, cols, 0, 0))

    def resize(self, cols, rows):
        self._set_size(self.master, max(1, min(int(cols), 500)), max(1, min(int(rows), 200)))

    def write(self, data):
        view = memoryview(data)
        while view:
            written = os.write(self.master, view)
            view = view[written:]

    def read(self, timeout=0.5):
        """Bytes, b'' if nothing yet, None at end of output."""
        ready, _, _ = select.select([self.master], [], [], timeout)
        if not ready:
            return b""
        try:
            data = os.read(self.master, 65536)
        except OSError:
            return None
        return data or None

    def close(self):
        try:
            os.killpg(self.proc.pid, signal.SIGHUP)
            deadline = time.monotonic() + 1.0
            while self.proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if self.proc.poll() is None:
                os.killpg(self.proc.pid, signal.SIGKILL)
            self.proc.wait(timeout=2)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass
        finally:
            try:
                os.close(self.master)
            except OSError:
                pass


def serve_socket(handler, *, token, argv=None, cwd=None):
    """Complete the WebSocket upgrade on a BaseHTTPRequestHandler and run one terminal session.
    The caller has already checked Host, Origin and the entry cookie."""
    key = handler.headers.get("Sec-WebSocket-Key", "")
    upgrade_ok = ("websocket" in handler.headers.get("Upgrade", "").lower()
                  and "upgrade" in handler.headers.get("Connection", "").lower()
                  and handler.headers.get("Sec-WebSocket-Version") == "13" and 16 <= len(key) <= 64)
    if not upgrade_ok:
        handler._send(400, "WebSocket upgrade required", "text/plain")
        return
    if not _sessions.acquire(blocking=False):
        handler._send(503, "Too many terminal sessions", "text/plain")
        return
    session = None
    try:
        handler.send_response(101, "Switching Protocols")
        handler.send_header("Upgrade", "websocket")
        handler.send_header("Connection", "Upgrade")
        handler.send_header("Sec-WebSocket-Accept", accept_key(key))
        handler.end_headers()
        handler.wfile.flush()
        handler.close_connection = True
        ws = WebSocket(handler.rfile, handler.wfile, max_message=MAX_INPUT)
        handler.connection.settimeout(AUTH_TIMEOUT)
        try:
            kind, payload = ws.recv()
            hello = json.loads(payload.decode("utf-8")) if kind == TEXT else {}
        except (WebSocketClosed, OSError, ValueError, UnicodeDecodeError):
            ws.close(1008)
            return
        import secrets
        if not (isinstance(hello, dict) and hello.get("t") == "auth"
                and secrets.compare_digest(str(hello.get("token", "")), token)):
            ws.close(1008)
            return
        handler.connection.settimeout(None)
        cols, rows = int(hello.get("cols") or 80), int(hello.get("rows") or 24)
        session = PtySession(argv or default_argv(), cwd or os.getcwd(),
                             max(1, min(cols, 500)), max(1, min(rows, 200)))
        done = threading.Event()

        def pump():
            try:
                while not done.is_set():
                    data = session.read()
                    if data is None:
                        break
                    if data:
                        ws.send(data, 0x2)
            except (OSError, ValueError, WebSocketClosed):
                pass
            finally:
                done.set()
                ws.close(1000)
                try:  # unblock the receive loop
                    handler.connection.shutdown(2)
                except OSError:
                    pass

        threading.Thread(target=pump, daemon=True).start()
        while not done.is_set():
            kind, payload = ws.recv()
            if kind != TEXT:
                continue
            message = json.loads(payload.decode("utf-8"))
            if message.get("t") == "i" and isinstance(message.get("d"), str):
                session.write(message["d"].encode("utf-8")[:MAX_INPUT])
            elif message.get("t") == "r":
                session.resize(message.get("c") or 80, message.get("r") or 24)
        done.set()
    except (WebSocketClosed, OSError, ValueError, UnicodeDecodeError):
        pass
    finally:
        if session is not None:
            session.close()
        _sessions.release()
