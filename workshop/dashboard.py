"""Local-only task dashboard. No remote exposure or automatic agent execution."""
import argparse
import html
import json
import os
import re
import secrets
import signal
import sys
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .adapters import find_opencode
from urllib.parse import parse_qs, urlsplit, quote
from .queue import Queue
from . import applog

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JetAI Hub</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:dark}
body{max-width:1000px;margin:32px auto;padding:0 18px;background:#111827;color:#e5e7eb}*{box-sizing:border-box}
h1{font-size:1.8rem}p{color:#9ca3af}
section,.job{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:18px;margin:14px 0}
input,select,button{padding:10px;border-radius:8px;border:1px solid #4b5563;background:#111827;color:#fff}
input{width:min(440px,85%)}button{cursor:pointer;background:#2563eb;border:0}
.job{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}.job>div:first-child{min-width:0;overflow-wrap:anywhere}.job form{max-width:100%}@media(max-width:600px){body{margin:12px auto;padding:0 12px}section,.job{padding:12px;margin:10px 0}input,select,button{max-width:100%;width:100%;min-height:44px;margin:4px 0}.job{display:block}}
small{color:#9ca3af} .tag{color:#93c5fd}
</style></head><body>
<h1>JetAI Hub</h1>
<p>Local task dashboard · A separately enabled worker executes queued tasks.<br>로컬 작업 대시보드 · 작업은 별도로 허용한 워커만 실행합니다.</p>
<section><h2>Add task / 작업 추가</h2><form method="post" action="/add">
<input type="hidden" name="token" value="__TOKEN__">
<select name="kind"><option>plan</option><option>code</option><option>qa</option></select>
<input name="title" placeholder="Describe a task" required maxlength="500">
<input name="resource" placeholder="Code: absolute worktree path" maxlength="2000">
<input name="model" placeholder="Model (optional, e.g. opencode/big-pickle-free or ollama/qwen2.5-coder)" maxlength="200">
<button>Add / 추가</button></form></section>
<h2>Tasks / 작업 목록</h2><p><a href="/setup" style="color:#93c5fd">Setup / 서비스 연결·테스트 →</a> · <a href="/boards" style="color:#93c5fd">Boards / 멀티 모델 보드 →</a></p>__JOBS__
</body></html>"""

STATE_KO = {"pending_approval": " (실행 승인 대기)", "queued": " (대기 중)",
            "running": " (실행 중)", "review": " (결과 검토 대기)",
            "done": " (완료)", "failed": " (실패 · 부수효과 확인 후 다시 추가)"}

def make_handler(queue, token, boards_root=None, providers=None, chat_fn=None, list_fn=None,
                 terminal_key=None, terminal_argv=None, terminal_cwd=None, chats_dir=None, api_ctx=None):
    boards_root = boards_root or str(Path.home() / ".agent-workshop" / "boards")
    from . import app_page, setup_page, terminal
    from .chat_api import ApiError, Context, Json, dispatch
    from .chat_store import ChatStore
    from .providers import Providers
    registry = providers or Providers()
    do_chat = chat_fn or setup_page.chat
    do_list = list_fn or setup_page.list_models

    api = api_ctx or Context(registry, ChatStore(chats_dir),
                             chat_fn=do_chat if chat_fn else setup_page.chat, list_fn=do_list)
    # The terminal WebSocket token is derived separately and only rendered for holders of the entry cookie,
    # so the page-wide API token (needed by the chat window) never unlocks the shell.
    terminal_token = hmac.new(token.encode(), b"terminal-websocket", hashlib.sha256).hexdigest()
    terminal_cookie = (hmac.new(token.encode(), terminal_key.encode(), hashlib.sha256).hexdigest()
                       if terminal_key else None)

    def setup_html(**kwargs):
        try:
            from .adapters import ollama_models
            ollama = ollama_models(timeout=1)
        except Exception:
            ollama = []
        return setup_page.render_page(registry, token, ollama_models=ollama,
                                      opencode_path=find_opencode(), **kwargs)
    class Handler(BaseHTTPRequestHandler):
        def _trusted_host(self):
            host = self.headers.get("Host", "")
            return host in (
                "127.0.0.1:" + str(self.server.server_port),
                "localhost:" + str(self.server.server_port),
            )

        def log_message(self, format, *args):
            # The one-time terminal entry key must not be echoed into logs.
            line = re.sub(r"(key=)[^&\s]+", r"\1***", format % args)
            line = "%s - - [%s] %s" % (self.address_string(), self.log_date_time_string(), line)
            print(line, flush=True)
            applog.write(line)

        def _json(self, status, payload):
            self._send(status, json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8",
                       csp="default-src 'none'")

        def _stream(self, events):
            """Server-sent events. Closing the generator (browser stop / disconnect) lets it save the partial reply."""
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Security-Policy", "default-src 'none'")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                for name, data in events:
                    self.wfile.write(("event: " + name + "\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
            finally:
                events.close()

        def _api(self, method):
            if not self._trusted_host():
                return self._json(403, {"error": "Invalid Host"})
            if self.headers.get("Origin") is not None and not self._origin_ok():
                return self._json(403, {"error": "Invalid Origin"})
            if not secrets.compare_digest(self.headers.get("X-CSRF-Token", ""), token):
                return self._json(403, {"error": "Forbidden"})
            body = {}
            try:
                size = int(self.headers.get("Content-Length", "0") or 0)
                if size < 0 or size > 262144:
                    return self._json(413, {"error": "Request too large"})
                if size:
                    if not self.headers.get("Content-Type", "").startswith("application/json"):
                        return self._json(415, {"error": "JSON required"})
                    body = json.loads(self.rfile.read(size).decode("utf-8"))
                result = dispatch(api, method, urlsplit(self.path).path[len("/api/v1"):], body)
            except ApiError as error:
                return self._json(error.status, {"error": error.message})
            except LookupError:
                return self._json(404, {"error": "Not found"})
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"error": "Invalid request"})
            if isinstance(result, Json):
                return self._json(result.status, result.payload)
            self._stream(result.events)

        def do_PATCH(self):
            if urlsplit(self.path).path.startswith("/api/v1/"):
                return self._api("PATCH")
            self._send(404, "Not found", "text/plain")

        def do_DELETE(self):
            if urlsplit(self.path).path.startswith("/api/v1/"):
                return self._api("DELETE")
            self._send(404, "Not found", "text/plain")

        def _embedded(self):
            # Browsers label requests for framed documents (also form posts/redirects inside the frame).
            return self.headers.get("Sec-Fetch-Dest") == "iframe"

        def _origin_ok(self):
            port = str(self.server.server_port)
            return self.headers.get("Origin") in ("http://127.0.0.1:" + port, "http://localhost:" + port)

        def _terminal_cookie_ok(self):
            if not terminal_cookie:
                return False
            for part in self.headers.get("Cookie", "").split(";"):
                name, _, value = part.strip().partition("=")
                if name == "workshop_term" and secrets.compare_digest(value, terminal_cookie):
                    return True
            return False

        def _send(self, code, body, content_type="text/html; charset=utf-8", csp=None, headers=None):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", csp or "default-src 'none'; style-src 'unsafe-inline'; img-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'self'")
            for name, value in (headers or {}):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._trusted_host():
                self._send(403, "Invalid Host", "text/plain")
                return
            if urlsplit(self.path).path == "/api/jobs":
                # Never expose prompts, workspace paths or execution logs without auth.
                public_jobs = [
                    {"id": job["id"], "kind": job["kind"], "title": job["title"],
                     "state": job["state"]} for job in queue.list()
                ]
                self._send(200, json.dumps(public_jobs), "application/json; charset=utf-8")
                return
            route = urlsplit(self.path).path
            if route.startswith("/api/v1/"):
                return self._api("GET")
            if route.startswith("/static/") and route[8:] in app_page.STATIC_FILES:
                name = route[8:]
                body = (Path(__file__).parent / "static" / name).read_bytes()
                self._send(200, body, app_page.STATIC_FILES[name],
                           csp="default-src 'none'; frame-ancestors 'self'")
                return
            if route == "/usage":
                from .usage import render_page as usage_page, summary_with_savings
                self._send(200, usage_page(summary_with_savings(api.ledger)))
                return
            if route == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if route == "/terminal/ws":
                # A shell as the current user: needs the feature on, loopback Host, a browser Origin from
                # this server, and the entry cookie that only the start-up key link hands out.
                if not terminal_key:
                    self._send(404, "Not found", "text/plain")
                elif not (self._origin_ok() and self._terminal_cookie_ok()):
                    self._send(403, "Forbidden", "text/plain")
                else:
                    terminal.serve_socket(self, token=terminal_token, argv=terminal_argv, cwd=terminal_cwd)
                return
            if route == "/app":
                supplied = parse_qs(urlsplit(self.path).query).get("key", [""])[0]
                if supplied:
                    if terminal_key and secrets.compare_digest(supplied, terminal_key):
                        self.send_response(303)
                        self.send_header("Set-Cookie", "workshop_term=" + terminal_cookie + "; HttpOnly; SameSite=Strict; Path=/")
                        self.send_header("Location", "/app")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                    else:
                        self._send(403, "Invalid key", "text/plain")
                    return
                state = "off" if not terminal_key else ("ready" if self._terminal_cookie_ok() else "locked")
                self._send(200, app_page.render(state, terminal_token if state == "ready" else "", token),
                           csp="default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                               "img-src 'self' data:; font-src 'self'; frame-src 'self'; "
                               "connect-src 'self' ws://127.0.0.1:" + str(self.server.server_port) +
                               " ws://localhost:" + str(self.server.server_port) + "; base-uri 'none'; "
                               "form-action 'self'; frame-ancestors 'none'")
                return
            if route == "/setup":
                self._send(200, setup_html(embedded=self._embedded()))
                return
            if route in ("/boards", "/board"):
                # Read-only view of shared boards; running models stays in the CLI/worker.
                root = Path(boards_root)
                if route == "/boards":
                    ids = sorted((d.name for d in root.glob("*") if (d / "board.md").is_file()), reverse=True) if root.is_dir() else []
                    items = "".join('<li><a href="/board?id=' + quote(i) + '">' + html.escape(i) + '</a></li>' for i in ids)
                    self._send(200, '<!doctype html><html lang="ko"><meta charset="utf-8"><body style="font-family:sans-serif;background:#111827;color:#e5e7eb;max-width:900px;margin:30px auto">'
                               '<h1>Boards / 보드</h1>' + ("" if self._embedded() else '<a href="/" style="color:#93c5fd">← Tasks</a>') + '<ul>' + (items or "<li>No boards yet. / 보드가 없습니다.</li>") + '</ul></body></html>')
                    return
                board_id = parse_qs(urlsplit(self.path).query).get("id", [""])[0]
                target = root / board_id / "board.md"
                if not re.fullmatch(r"[0-9a-f-]{36}", board_id) or not target.is_file():
                    self._send(404, "Not found", "text/plain")
                    return
                self._send(200, '<!doctype html><html lang="ko"><meta charset="utf-8"><body style="font-family:sans-serif;background:#111827;color:#e5e7eb;max-width:900px;margin:30px auto">'
                           '<a href="/boards" style="color:#93c5fd">← Boards</a><pre style="white-space:pre-wrap;overflow-wrap:anywhere">' +
                           html.escape(target.read_text(encoding="utf-8")[:200000]) + '</pre></body></html>')
                return
            if route != "/":
                self._send(404, "Not found", "text/plain")
                return
            jobs = queue.list()
            cards = []
            for job in reversed(jobs):
                name = html.escape(job["title"])
                kind = html.escape(job["kind"])
                state = html.escape(job["state"])
                approve = ""
                if job["state"] == "pending_approval":
                    approve = ('<form method="post" action="/authorize">'
                               '<input type="hidden" name="token" value="' + token + '">'
                               '<input type="hidden" name="id" value="' + html.escape(job["id"]) + '">'
                               '<button>Authorize execution / 실행 승인</button></form>')
                if job["state"] == "review":
                    approve = ('<form method="post" action="/approve">'
                               '<input type="hidden" name="token" value="' + token + '">'
                               '<input type="hidden" name="id" value="' + html.escape(job["id"]) + '">'
                               '<button>Approve / 결과 승인</button></form>')
                detail = ""
                cards.append('<div class="job"><div><strong>' + name + '</strong><br>'
                             '<small>' + kind + ' · <span class="tag">' + state + STATE_KO.get(job["state"], "") + '</span></small></div>'
                             + detail + approve + '</div>')
            page = PAGE.replace("__TOKEN__", token).replace("__JOBS__", "".join(cards) or "<p>No tasks yet. / 아직 작업이 없습니다.</p>")
            if self._embedded():
                page = re.sub(r'<p><a href="/setup".*?</p>', "", page, count=1, flags=re.S)
            self._send(200, page)

        def do_POST(self):
            if not self._trusted_host():
                self._send(403, "Invalid Host", "text/plain")
                return
            path = urlsplit(self.path).path
            if path.startswith("/api/v1/"):
                return self._api("POST")
            if path not in ("/add", "/approve", "/authorize", "/setup/run", "/setup/remove", "/setup/models"):
                self._send(404, "Not found", "text/plain")
                return
            try:
                origin = self.headers.get("Origin")
                if origin is not None:
                    allowed_origins = (
                        "http://127.0.0.1:" + str(self.server.server_port),
                        "http://localhost:" + str(self.server.server_port),
                    )
                    if origin not in allowed_origins:
                        self._send(403, "Invalid Origin", "text/plain")
                        return
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > (32768 if path.startswith("/setup/") else 8192):
                    raise ValueError("Invalid request size")
                form = parse_qs(self.rfile.read(size).decode("utf-8"), keep_blank_values=True)
                if not secrets.compare_digest(form.get("token", [""])[0], token):
                    self._send(403, "Forbidden", "text/plain")
                    return
                if path == "/setup/run":
                    self._send(200, setup_html(result=setup_page.run_test(registry, form, do_chat), embedded=self._embedded()))
                    return
                if path == "/setup/models":
                    try:
                        listing = setup_page.run_models(registry, form.get("name", [""])[0], do_list)
                        self._send(200, setup_html(models=listing, embedded=self._embedded()))
                    except (ValueError, RuntimeError, OSError) as error:
                        self._send(200, setup_html(notice="모델 목록 실패: " + str(error), embedded=self._embedded()))
                    return
                if path == "/setup/remove":
                    registry.remove(form.get("name", [""])[0])
                    self.send_response(303)
                    self.send_header("Location", "/setup")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if path == "/add":
                    kind = form.get("kind", [""])[0]
                    title = form.get("title", [""])[0][:500]
                    resource = form.get("resource", [""])[0].strip()
                    if kind == "code" and not resource:
                        raise ValueError("Code tasks require a workspace path")
                    payload = {"workspace": resource} if kind == "code" else {}
                    model = form.get("model", [""])[0].strip()
                    if model and kind == "code":
                        payload["model"] = model
                    queue.add(kind, title, payload, require_approval=kind == "code")
                elif path == "/authorize":
                    queue.authorize(form.get("id", [""])[0])
                else:
                    queue.approve(form.get("id", [""])[0])
            except (ValueError, UnicodeDecodeError, IndexError) as error:
                self._send(400, str(error), "text/plain")
                return
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Content-Length", "0")
            self.end_headers()
    return Handler

def main():
    parser = argparse.ArgumentParser(description="Local-only JetAI Hub dashboard")
    parser.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--enable-terminal", action="store_true",
                        help="Enable the in-browser WSL shell (runs commands as YOUR user; entry needs a start-up key)")
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    terminal_key = None
    if args.enable_terminal:
        terminal_key = secrets.token_urlsafe(24)
        key_file = Path.home() / ".agent-workshop" / "terminal.key"
        key_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(terminal_key)
        os.chmod(key_file, 0o600)
    applog.setup()
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(
            Queue(args.db), token, terminal_key=terminal_key, terminal_cwd=str(Path(__file__).resolve().parent.parent)))
    except OSError as error:
        if error.errno in (98, 10048, 48) or "Address already in use" in str(error):
            print("포트 %d를 이미 다른 프로그램이 쓰고 있습니다. JetAI Hub가 이미 실행 중일 수 있습니다: "
                  "http://127.0.0.1:%d/app 을 열어 보세요. 아니라면 --port 로 다른 포트를 지정하세요." % (args.port, args.port),
                  file=sys.stderr, flush=True)
            raise SystemExit(2)
        raise
    applog.write("start port=%d terminal=%s" % (args.port, "on" if terminal_key else "off"))
    # A termination request (closing the window, `kill`) should stop the server cleanly like Ctrl+C does.
    def _stop(*_):
        raise KeyboardInterrupt
    for name in ("SIGTERM", "SIGBREAK"):
        if hasattr(signal, name):
            try:
                signal.signal(getattr(signal, name), _stop)
            except (ValueError, OSError):
                pass
    print(f"Dashboard: http://127.0.0.1:{args.port}/", flush=True)
    print(f"Setup (connect a service and test a prompt): http://127.0.0.1:{args.port}/setup", flush=True)
    print(f"App window (tabs): http://127.0.0.1:{args.port}/app", flush=True)
    if terminal_key:
        print("TERMINAL ENABLED: a shell running as your user. Open it once with this private link "
              "(do not share it):", flush=True)
        print(f"  http://127.0.0.1:{args.port}/app?key={terminal_key}", flush=True)
    print("Loopback only. Stop with Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        applog.write("stop")

if __name__ == "__main__":
    main()
