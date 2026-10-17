"""Local-only task dashboard. No remote exposure or automatic agent execution."""
import argparse
import html
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from .queue import Queue

PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent Workshop Hub</title>
<style>
:root{font-family:system-ui,sans-serif;color-scheme:dark}
body{max-width:1000px;margin:32px auto;padding:0 18px;background:#111827;color:#e5e7eb}
h1{font-size:1.8rem}p{color:#9ca3af}
section,.job{background:#1f2937;border:1px solid #374151;border-radius:12px;padding:18px;margin:14px 0}
input,select,button{padding:10px;border-radius:8px;border:1px solid #4b5563;background:#111827;color:#fff}
input{width:min(440px,85%)}button{cursor:pointer;background:#2563eb;border:0}
.job{display:flex;justify-content:space-between;gap:14px;align-items:center;flex-wrap:wrap}
small{color:#9ca3af} .tag{color:#93c5fd}
</style></head><body>
<h1>Agent Workshop Hub</h1>
<p>Local task dashboard · A separately enabled worker executes queued tasks.</p>
<section><h2>Add task</h2><form method="post" action="/add">
<input type="hidden" name="token" value="__TOKEN__">
<select name="kind"><option>plan</option><option>code</option><option>image</option><option>qa</option></select>
<input name="title" placeholder="Describe a task" required maxlength="500">
<input name="resource" placeholder="Code: absolute worktree path / Image: workflow JSON path" maxlength="2000">
<button>Add</button></form></section>
<h2>Tasks</h2>__JOBS__
</body></html>"""

def make_handler(queue, token):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, content_type="text/html; charset=utf-8"):
            data = body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if urlsplit(self.path).path == "/api/jobs":
                self._send(200, json.dumps(queue.list()), "application/json; charset=utf-8")
                return
            if urlsplit(self.path).path != "/":
                self._send(404, "Not found", "text/plain")
                return
            jobs = queue.list()
            cards = []
            for job in reversed(jobs):
                name = html.escape(job["title"])
                kind = html.escape(job["kind"])
                state = html.escape(job["state"])
                approve = ""
                if job["state"] == "review":
                    approve = ('<form method="post" action="/approve">'
                               '<input type="hidden" name="token" value="' + token + '">'
                               '<input type="hidden" name="id" value="' + html.escape(job["id"]) + '">'
                               '<button>Approve</button></form>')
                cards.append('<div class="job"><div><strong>' + name + '</strong><br>'
                             '<small>' + kind + ' · <span class="tag">' + state + '</span></small></div>'
                             + approve + '</div>')
            page = PAGE.replace("__TOKEN__", token).replace("__JOBS__", "".join(cards) or "<p>No tasks yet.</p>")
            self._send(200, page)

        def do_POST(self):
            path = urlsplit(self.path).path
            if path not in ("/add", "/approve"):
                self._send(404, "Not found", "text/plain")
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size <= 0 or size > 8192:
                    raise ValueError("Invalid request size")
                form = parse_qs(self.rfile.read(size).decode("utf-8"), keep_blank_values=True)
                if not secrets.compare_digest(form.get("token", [""])[0], token):
                    self._send(403, "Forbidden", "text/plain")
                    return
                if path == "/add":
                    kind = form.get("kind", [""])[0]
                    title = form.get("title", [""])[0][:500]
                    resource = form.get("resource", [""])[0].strip()
                    if kind in ("code", "image") and not resource:
                        raise ValueError("Code and image tasks require a workspace or workflow path")
                    payload = ({"workspace": resource} if kind == "code" else
                               {"workflow": resource} if kind == "image" else {})
                    queue.add(kind, title, payload)
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
    parser = argparse.ArgumentParser(description="Local-only Agent Workshop dashboard")
    parser.add_argument("--db", default=str(Path.home() / ".agent-workshop" / "jobs.sqlite3"))
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    token = secrets.token_urlsafe(32)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(Queue(args.db), token))
    print(f"Dashboard: http://127.0.0.1:{args.port}/", flush=True)
    print("Loopback only. Stop with Ctrl+C.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()
