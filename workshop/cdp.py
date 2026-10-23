"""Browser control through the Chrome DevTools Protocol, standard library only (plan D1: no Playwright).

Starts the Chrome/Edge that is already installed with a throw-away profile (no download, nothing of the user's own
profile or sign-ins), speaks CDP over a small WebSocket client, and offers two kinds of calls:
  - observe (always allowed): page text, a list of clickable/typeable elements, a screenshot, console and network errors;
  - act (click, type): refused in "observe" mode, in "approval" mode only after `approve(description)` says yes.
Page text is data. It is returned with `untrusted: True` and never turns into an instruction. Only localhost pages
open unless a host was allowed on purpose.
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.parse
from pathlib import Path

from .websocket import GUID, TEXT, BINARY, CLOSE, PING, PONG, CONT

LOCAL_HOSTS = ("localhost", "127.0.0.1", "[::1]", "::1")
MAX_MESSAGE = 32 << 20                       # screenshots arrive as one base64 message
TEXT_LIMIT = 6000
ELEMENT_LIMIT = 60


class CdpError(Exception):
    pass


class WSClient:
    """RFC 6455 client: masked frames out, fragments/ping/close in."""

    def __init__(self, host, port, path, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (path, host, port, key)).encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise CdpError("연결이 끊겼습니다(핸드셰이크)")
            head += chunk
            if len(head) > 16384:
                raise CdpError("핸드셰이크 응답이 너무 깁니다")
        header, _, self._buffer = head.partition(b"\r\n\r\n")
        if b" 101 " not in header.split(b"\r\n")[0]:
            raise CdpError("웹소켓 연결이 거부되었습니다")
        expected = base64.b64encode(__import__("hashlib").sha1((key + GUID).encode()).digest()).decode().lower()
        if expected not in header.decode("latin-1").lower():
            raise CdpError("웹소켓 응답 키가 맞지 않습니다")

    def _read(self, count):
        while len(self._buffer) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("연결이 끊겼습니다")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def send(self, text):
        payload = text.encode("utf-8")
        header = bytes([0x80 | TEXT])
        size = len(payload)
        if size < 126:
            header += bytes([0x80 | size])
        elif size < 1 << 16:
            header += bytes([0x80 | 126]) + struct.pack(">H", size)
        else:
            header += bytes([0x80 | 127]) + struct.pack(">Q", size)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def recv(self, timeout=None):
        if timeout is not None:
            self.sock.settimeout(timeout)
        message, started = b"", False
        while True:
            first, second = self._read(2)
            fin, opcode, size = first & 0x80, first & 0x0F, second & 0x7F
            if size == 126:
                size = struct.unpack(">H", self._read(2))[0]
            elif size == 127:
                size = struct.unpack(">Q", self._read(8))[0]
            if size > MAX_MESSAGE:
                raise CdpError("메시지가 너무 큽니다")
            payload = self._read(size)
            if opcode == PING:
                continue
            if opcode == CLOSE:
                raise CdpError("브라우저가 연결을 닫았습니다")
            if opcode in (TEXT, BINARY, CONT):
                message += payload
                started = True
                if fin:
                    return message.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Session:
    """One CDP target: request/response by id, events kept in a bounded list."""

    def __init__(self, ws):
        self.ws, self.next_id, self.events = ws, 0, []

    def call(self, method, params=None, timeout=15):
        self.next_id += 1
        want = self.next_id
        self.ws.send(json.dumps({"id": want, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise CdpError("%s 응답을 기다리다 시간이 지났습니다" % method)
            try:
                message = json.loads(self.ws.recv(left))
            except socket.timeout:
                raise CdpError("%s 응답을 기다리다 시간이 지났습니다" % method)
            if message.get("id") == want:
                if "error" in message:
                    raise CdpError("%s: %s" % (method, str(message["error"].get("message", message["error"]))[:200]))
                return message.get("result") or {}
            if "method" in message:
                self.events.append(message)
                del self.events[:-300]

    def drain(self, wait=0.0):
        """Pick up events that arrived while nobody was calling."""
        end = time.time() + wait
        while True:
            try:
                self.events.append(json.loads(self.ws.recv(max(0.01, end - time.time()))))
            except (socket.timeout, CdpError):
                break
            if time.time() >= end:
                break
        del self.events[:-300]


_PAGE_SCRIPT = """(() => {
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const sel = 'a[href],button,input,select,textarea,[role=button],[onclick]';
  const items = [];
  document.querySelectorAll(sel).forEach((e, i) => { if (items.length >= %d || !vis(e)) return;
    if (!e.dataset.jetaiRef) e.dataset.jetaiRef = 'e' + i;
    items.push({ref: e.dataset.jetaiRef, tag: e.tagName.toLowerCase(), type: e.type || '',
      text: (e.innerText || e.value || e.getAttribute('aria-label') || e.placeholder || '').trim().slice(0, 80) }); });
  return JSON.stringify({title: document.title, url: location.href,
    text: (document.body ? document.body.innerText : '').slice(0, %d), elements: items});
})()""" % (ELEMENT_LIMIT, TEXT_LIMIT)


def find_browser():
    names = ("chrome.exe", "msedge.exe")
    roots = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
    for root in roots:
        for rel in (r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"):
            candidate = Path(root) / rel if root else None
            if candidate and candidate.exists():
                return str(candidate)
    for name in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge") + names:
        found = shutil.which(name)
        if found:
            return found
    return None


def host_allowed(url, allow_hosts=()):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1") or host in [h.lower() for h in allow_hosts]


class Browser:
    """mode: "observe" (no clicks or typing) or "approval" (each action needs approve(description) -> True)."""

    def __init__(self, *, mode="observe", approve=None, allow_hosts=(), binary=None, popen=subprocess.Popen, headless=True):
        self.mode, self.approve, self.allow_hosts = mode, approve, tuple(allow_hosts)
        self.binary, self.popen, self.headless = binary or find_browser(), popen, headless
        self.proc = self.session = self.profile = None
        self.log = []                                                         # what was done, for the audit trail

    # --- life cycle -------------------------------------------------------------------------
    def start(self, timeout=20):
        if not self.binary:
            raise CdpError("설치된 Chrome 또는 Edge를 찾지 못했습니다")
        self.profile = tempfile.mkdtemp(prefix="jetai-browser-")
        args = [self.binary, "--remote-debugging-port=0", "--user-data-dir=" + self.profile, "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--disable-sync", "about:blank"]
        if self.headless:
            args.insert(1, "--headless=new")
        self.proc = self.popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port_file = Path(self.profile) / "DevToolsActivePort"
        end = time.time() + timeout
        while time.time() < end and not port_file.exists():
            time.sleep(0.1)
        if not port_file.exists():
            self.stop()
            raise CdpError("브라우저가 제때 준비되지 않았습니다")
        port = int(port_file.read_text().split()[0])
        self.attach(port)
        return port

    def attach(self, port):
        import urllib.request
        with urllib.request.urlopen("http://127.0.0.1:%d/json/list" % port, timeout=10) as reply:
            targets = [t for t in json.loads(reply.read()) if t.get("type") == "page"]
        if not targets:
            raise CdpError("열린 탭이 없습니다")
        path = urllib.parse.urlparse(targets[0]["webSocketDebuggerUrl"]).path
        self.session = Session(WSClient("127.0.0.1", port, path))
        for domain in ("Page", "Runtime", "Network", "Log"):
            self.session.call(domain + ".enable")

    def stop(self):
        if self.session:
            self.session.ws.close()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)
        self.proc = self.session = self.profile = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()

    # --- observe ----------------------------------------------------------------------------
    def open(self, url, wait=8):
        if not host_allowed(url, self.allow_hosts):
            raise CdpError("허용되지 않은 주소입니다(localhost만 기본 허용): " + url[:80])
        self.session.call("Page.navigate", {"url": url})
        end = time.time() + wait
        while time.time() < end:
            if self.session.call("Runtime.evaluate", {"expression": "document.readyState", "returnByValue": True}) \
                    .get("result", {}).get("value") == "complete":
                break
            time.sleep(0.1)
        self.log.append(("open", url))

    def observe(self):
        """-> {"title","url","text","elements":[{"ref","tag","type","text"}],"untrusted":True}"""
        raw = self.session.call("Runtime.evaluate", {"expression": _PAGE_SCRIPT, "returnByValue": True})
        value = raw.get("result", {}).get("value")
        if not isinstance(value, str):
            raise CdpError("페이지를 읽지 못했습니다")
        page = json.loads(value)
        page["untrusted"] = True
        return page

    def screenshot(self, path):
        data = self.session.call("Page.captureScreenshot", {"format": "png"}).get("data", "")
        Path(path).write_bytes(base64.b64decode(data))
        return Path(path).stat().st_size

    def problems(self):
        """Console errors, uncaught exceptions and failed requests seen so far."""
        self.session.drain()
        found = []
        for event in self.session.events:
            method, params = event.get("method"), event.get("params") or {}
            if method == "Runtime.exceptionThrown":
                found.append("예외: " + str((params.get("exceptionDetails") or {}).get("text", ""))[:160])
            elif method == "Runtime.consoleAPICalled" and params.get("type") in ("error", "assert"):
                found.append("콘솔 오류: " + " ".join(str(a.get("value", a.get("description", ""))) for a in params.get("args", []))[:160])
            elif method == "Network.loadingFailed":
                found.append("요청 실패: " + str(params.get("errorText", ""))[:80])
            elif method == "Log.entryAdded" and (params.get("entry") or {}).get("level") == "error":
                found.append("로그 오류: " + str(params["entry"].get("text", ""))[:160])
        return found

    # --- act --------------------------------------------------------------------------------
    def _allowed(self, description):
        if self.mode == "approval" and self.approve and self.approve(description):
            self.log.append(("approved", description))
            return True
        self.log.append(("refused", description))
        return False

    def click(self, ref):
        if not self._allowed("클릭 " + ref):
            raise CdpError("관찰 모드이거나 승인되지 않아 클릭하지 않았습니다")
        return self._act(ref, "e.click(); return true;")

    def type_text(self, ref, text):
        if not self._allowed("입력 %s (%d자)" % (ref, len(text))):
            raise CdpError("관찰 모드이거나 승인되지 않아 입력하지 않았습니다")
        return self._act(ref, "e.focus(); e.value = %s; e.dispatchEvent(new Event('input', {bubbles: true})); return true;" % json.dumps(text))

    def _act(self, ref, body):
        if not isinstance(ref, str) or not ref.replace("e", "", 1).isdigit():
            raise CdpError("올바르지 않은 요소 번호입니다")
        script = "(() => { const e = document.querySelector('[data-jetai-ref=%s]'); if (!e) return false; %s })()" % (ref, body)
        ok = self.session.call("Runtime.evaluate", {"expression": script, "returnByValue": True}).get("result", {}).get("value")
        if not ok:
            raise CdpError("요소를 찾지 못했습니다: " + ref)
        return True
